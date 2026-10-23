"""
Standalone Vertex AI LLM client (Google Gen AI SDK, `google-genai`).
Configuration from constructor arguments, else the environment / .env.

    VERTEX_SERVICE_ACCOUNT_FILE  - path to GCP service account JSON
    VERTEX_PROJECT_ID            - (optional) GCP project ID; read from the SA file if omitted
    VERTEX_LOCATION              - default "global" (where Gemini 3 preview models are served)
    VERTEX_MODEL_NAME            - default gemini-3-pro-preview
    VERTEX_MAX_RETRY             - int, default 3
    VERTEX_TEMPERATURE           - float; default 1.0 for Gemini 3 (Google's advice: lower
                                   values can loop or degrade it), 0 for older models
    VERTEX_TIMEOUT_SEC           - per request, default 120

Moved off vertexai.generative_models (22 Oct 2026): Google has deprecated that
module in favour of google-genai, and it does not reach Gemini 3. The public
methods are unchanged; query_json (structured JSON answers) is new.

GeminiAPILLM (below) is the same client on the Gemini Developer API with an
API key (GEMINI_API_KEY) in place of the service account.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                     # optional: the constructor takes everything
    pass

from google import genai
from google.auth.transport.requests import Request
from google.genai import types
from google.oauth2 import service_account

# Library code: no logging.basicConfig here - it would reconfigure the logging
# of whatever process imports this (the trading engine does).
logger = logging.getLogger("vertex_ai")

# ---------------------------------------------------------------------------
# Token / cost config (USD per token)
# ---------------------------------------------------------------------------
TOKEN_LIMIT = {
    "gemini-3-pro-preview": {
        "output": 65536,
        "input_cost": 0.000002,         # <= 200k context
        "output_cost": 0.000012,        # thinking tokens are billed as output
        "input_cost_ext": 0.000004,     # > 200k context
        "output_cost_ext": 0.000018,
    },
    # Assumed at gemini-3-pro-preview's rates (its successor on the Gemini
    # API); check Google's price list - the dashboard shows it as an estimate.
    "gemini-3.1-pro-preview": {
        "output": 65536,
        "input_cost": 0.000002,
        "output_cost": 0.000012,
        "input_cost_ext": 0.000004,
        "output_cost_ext": 0.000018,
    },
    "gemini-2.5-pro": {
        "output": 65000,
        "input_cost": 0.00000125,       # per token, <=200k ctx
        "output_cost": 0.000010,
        "input_cost_ext": 0.0000025,    # per token, >200k ctx
        "output_cost_ext": 0.000015,
    },
    "gemini-2.5-flash": {
        "output": 65000,
        "input_cost": 0.000000075,
        "output_cost": 0.0000003,
        "input_cost_ext": 0.000000075,
        "output_cost_ext": 0.0000003,
    },
    "gemini-2.5-flash-lite": {
        "output": 65000,
        "input_cost": 0.000000075,
        "output_cost": 0.0000003,
        "input_cost_ext": 0.000000075,
        "output_cost_ext": 0.0000003,
    },
}


# ---------------------------------------------------------------------------
# Helper: read env with optional default / type cast
# ---------------------------------------------------------------------------
def _env(key: str, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        return None
    try:
        return cast(val)
    except (ValueError, TypeError):
        return default


def _is_gemini3(model_name: str) -> bool:
    return str(model_name).startswith("gemini-3")


# ---------------------------------------------------------------------------
# VertexAILLM
# ---------------------------------------------------------------------------
class VertexAILLM:
    """
    Standalone (no base class, no factory) Vertex AI client.

    HOW TO CREATE SERVICE ACCOUNT JSON FILE:
      1. Go to Google Cloud Console (console.cloud.google.com)
      2. Select your project → IAM & Admin → Service Accounts
      3. Create Service Account (name: etl-gemini-api)
      4. Assign roles:
           - Vertex AI User (roles/aiplatform.user)
           - Service Account Token Creator (roles/iam.serviceAccountTokenCreator)
      5. Keys tab → Add Key → Create new key → JSON → Download
      6. APIs & Services → enable "Vertex AI API" (aiplatform.googleapis.com) for the project
    """

    def __init__(
        self,
        service_account_file: Optional[str] = None,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        model_name: Optional[str] = None,
        max_retries: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout_sec: Optional[float] = None,
        refresh_before_seconds: int = 900,
        retry_delay: float = 1.0,
    ):
        service_account_file = service_account_file or _env("VERTEX_SERVICE_ACCOUNT_FILE")
        if not service_account_file:
            raise ValueError(
                "VERTEX_SERVICE_ACCOUNT_FILE is not set. "
                "Pass service_account_file or add it to your .env file."
            )

        self.service_account_file   = service_account_file
        self.project_id             = project_id or _env("VERTEX_PROJECT_ID")
        self.location               = location or _env("VERTEX_LOCATION", "global")
        self.model_name             = model_name or _env("VERTEX_MODEL_NAME", "gemini-3-pro-preview")
        self.max_retries            = _env("VERTEX_MAX_RETRY", 3, int) if max_retries is None else int(max_retries)
        self.temperature            = temperature if temperature is not None else _env("VERTEX_TEMPERATURE", None, float)
        self.timeout_sec            = float(timeout_sec or _env("VERTEX_TIMEOUT_SEC", 120.0, float))
        self.retry_delay            = retry_delay
        self.refresh_before_seconds = refresh_before_seconds

        self.credentials  = None
        self.token_expiry = None
        self.client       = None
        self._initialized = False

        if not os.path.exists(service_account_file):
            raise ValueError(
                f"Service account file not found: {service_account_file}"
            )

        # Auto-read project_id from SA file when not provided
        if not self.project_id:
            try:
                with open(service_account_file) as f:
                    sa_info = json.load(f)
                self.project_id = sa_info.get("project_id")
                if not self.project_id:
                    raise ValueError("project_id missing in service account file")
            except Exception as exc:
                raise ValueError(
                    "Unable to extract project_id from service account file"
                ) from exc

        self._initialize()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _connect(self):
        self.client = genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
            credentials=self.credentials,
            http_options=types.HttpOptions(timeout=int(self.timeout_sec * 1000)),
        )

    def _initialize(self):
        try:
            self.credentials = service_account.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            if not self.credentials.valid:
                self.credentials.refresh(Request())
            self.token_expiry = self.credentials.expiry
            self._connect()
            logger.info(
                "Initialized Vertex AI | project=%s | location=%s | model=%s",
                self.project_id, self.location, self.model_name,
            )
            self._initialized = True
        except Exception:
            logger.exception("Failed to initialize Vertex AI client")
            raise

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------
    def _ensure_valid_token(self) -> bool:
        """Refresh credentials if they expire within refresh_before_seconds. Returns True if refreshed."""
        if not self.token_expiry:
            return False
        expiry = self.token_expiry
        now = datetime.now(timezone.utc) if expiry.tzinfo else datetime.utcnow()
        if expiry - now >= timedelta(seconds=self.refresh_before_seconds):
            return False
        try:
            logger.info("Token expiring soon — refreshing …")
            self.credentials.refresh(Request())
            self.token_expiry = self.credentials.expiry
            self._connect()
            return True
        except Exception:
            logger.exception("Failed to refresh token")
            return False

    def _handle_auth_error(self, error: Exception):
        """Force-refresh credentials on auth-related errors."""
        if any(
            kw in str(error).lower()
            for kw in ("unauthorized", "unauthenticated", "authentication", "token", "auth")
        ):
            logger.info("Auth error detected — refreshing token …")
            try:
                self.credentials.refresh(Request())
                self.token_expiry = self.credentials.expiry
                self._connect()
            except Exception:
                logger.exception("Token refresh after auth error failed")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def get_model(self):
        """The google-genai client (client.models.generate_content(...))."""
        if not self._initialized or self.client is None:
            raise RuntimeError("Vertex AI client not properly initialized")
        self._ensure_valid_token()
        return self.client

    def calculate_gemini_cost(
        self, model_name: str, input_tokens: Optional[int], output_tokens: Optional[int]
    ) -> float:
        config = TOKEN_LIMIT.get(model_name)
        if not config or input_tokens is None or output_tokens is None:
            return 0.0
        if model_name in ("gemini-2.5-pro", "gemini-3-pro-preview", "gemini-3.1-pro-preview") \
                and input_tokens > 200_000:
            in_rate  = config["input_cost_ext"]
            out_rate = config["output_cost_ext"]
        else:
            in_rate  = config["input_cost"]
            out_rate = config["output_cost"]
        return round(input_tokens * in_rate + output_tokens * out_rate, 6)

    def _default_generation_config(self, model_name: str) -> dict:
        max_tokens = TOKEN_LIMIT.get(model_name, TOKEN_LIMIT["gemini-2.5-pro"]).get("output", 65000)
        if _is_gemini3(model_name):
            return {
                "temperature": self.temperature if self.temperature is not None else 1.0,
                "candidate_count": 1,
                "max_output_tokens": max_tokens,
            }
        return {
            "temperature":      self.temperature if self.temperature is not None else 0,
            "top_p":            1,
            "top_k":            1,
            "candidate_count":  1,
            "max_output_tokens": max_tokens,
            "seed":             42,
        }

    def _config(self, model_name, generation_config=None, **extra) -> types.GenerateContentConfig:
        """GenerateContentConfig from the defaults, a caller's generation_config
        dict (the old vertexai keyword) and any GenerateContentConfig fields."""
        cfg = self._default_generation_config(model_name)
        cfg.update(generation_config or {})
        cfg.update({k: v for k, v in extra.items() if v is not None})
        return types.GenerateContentConfig(**cfg)

    def _generate(self, contents, model_name, what, **kwargs):
        """generate_content with retries; returns the response."""
        if not self._initialized:
            self._initialize()
        self._ensure_valid_token()
        model_name = model_name or self.model_name
        config = self._config(model_name, **kwargs)
        retries = 0
        while True:
            try:
                t0 = time.time()
                response = self.client.models.generate_content(
                    model=model_name, contents=contents, config=config)
                logger.info(
                    "%s | model=%s | output_len=%d | elapsed_ms=%.0f",
                    what, model_name, len(response.text or ""), (time.time() - t0) * 1000,
                )
                return response
            except Exception as exc:
                retries += 1
                logger.warning("%s error (attempt %d/%d): %s", what, retries, self.max_retries + 1, exc)
                self._handle_auth_error(exc)
                if retries > self.max_retries:
                    raise
                time.sleep(self.retry_delay * (2 ** (retries - 1)))

    # ------------------------------------------------------------------
    # query  (text → text)
    # ------------------------------------------------------------------
    def query(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """Send a text prompt and return the model's text response."""
        return self._generate(prompt, model_name, "query", **kwargs).text

    # ------------------------------------------------------------------
    # query_json  (text → parsed JSON, with usage and cost)
    # ------------------------------------------------------------------
    def query_json(
        self,
        prompt: str,
        schema: Optional[dict] = None,
        model_name: Optional[str] = None,
        system_instruction: Optional[str] = None,
        thinking_level: Optional[str] = None,
        **kwargs,
    ):
        """Ask for a JSON answer (optionally held to a JSON schema).

        Returns (parsed JSON, meta) - meta: model, input_tokens,
        output_tokens, thinking_tokens, cost_usd, elapsed_s. thinking_level
        ("low" / "high" on Gemini 3) sets how much the model reasons first.
        Raises when no valid JSON comes back."""
        model_name = model_name or self.model_name
        thinking = None
        if thinking_level and _is_gemini3(model_name):
            thinking = types.ThinkingConfig(thinking_level=str(thinking_level).upper())
        t0 = time.time()
        response = self._generate(
            prompt, model_name, "query_json",
            response_mime_type="application/json",
            response_json_schema=schema,
            system_instruction=system_instruction,
            thinking_config=thinking,
            **kwargs,
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(text)
        u = response.usage_metadata
        inp = getattr(u, "prompt_token_count", None)
        out = (getattr(u, "candidates_token_count", None) or 0) + (getattr(u, "thoughts_token_count", None) or 0)
        meta = {
            "model": model_name,
            "input_tokens": inp,
            "output_tokens": getattr(u, "candidates_token_count", None),
            "thinking_tokens": getattr(u, "thoughts_token_count", None),
            "cost_usd": self.calculate_gemini_cost(model_name, inp, out),
            "elapsed_s": round(time.time() - t0, 2),
        }
        return data, meta

    # ------------------------------------------------------------------
    # generate_text  (alias with prompt-size logging)
    # ------------------------------------------------------------------
    def generate_text(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """Alias for query() — logs the prompt size and returns text."""
        logger.info("generate_text | model=%s | prompt_len=%d", model_name or self.model_name, len(prompt))
        return self.query(prompt, model_name=model_name, **kwargs)

    # ------------------------------------------------------------------
    # query_with_files  (multimodal: text + raw bytes)
    # ------------------------------------------------------------------
    def query_with_files(
        self,
        prompt: str,
        file_data: bytes,
        mime_type: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        Send a prompt with a single binary file attachment.

        Args:
            prompt:     Text instruction / question.
            file_data:  Raw bytes of the file.
            mime_type:  MIME type string, e.g. "application/pdf" or "image/png".
            model_name: Vertex AI model to use.

        Returns:
            Model response text.
        """
        parts = [types.Part.from_text(text=prompt), types.Part.from_bytes(data=file_data, mime_type=mime_type)]
        try:
            return self._generate(parts, model_name, "query_with_files", **kwargs).text
        except Exception as exc:
            raise RuntimeError(f"Failed after {self.max_retries} retries: {exc}") from exc

    # ------------------------------------------------------------------
    # query_with_file_path  (convenience: pass a file path, not bytes)
    # ------------------------------------------------------------------

    # Supported extensions → MIME types
    _MIME_MAP: dict = {
        # Documents
        ".pdf":  "application/pdf",
        # Images
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".webp": "image/webp",
        ".bmp":  "image/bmp",
        ".tiff": "image/tiff",
        ".tif":  "image/tiff",
        # Video
        ".mp4":  "video/mp4",
        ".mov":  "video/quicktime",
        ".avi":  "video/avi",
        ".mkv":  "video/x-matroska",
        # Audio
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".flac": "audio/flac",
        ".m4a":  "audio/mp4",
        # Text / code (sent as plain text parts)
        ".txt":  "text/plain",
        ".csv":  "text/csv",
        ".html": "text/html",
        ".xml":  "text/xml",
    }

    def query_with_file_path(
        self,
        prompt: str,
        file_path: str,
        mime_type: Optional[str] = None,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        Send a prompt together with a local file — just pass the path.
        MIME type is auto-detected from the file extension; override with
        the ``mime_type`` argument if needed.

        Example:
            response = client.query_with_file_path(
                prompt="Summarise this document.",
                file_path="/reports/q3.pdf",
            )
        """
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        if mime_type is None:
            ext = os.path.splitext(file_path)[1].lower()
            mime_type = self._MIME_MAP.get(ext)
            if mime_type is None:
                raise ValueError(
                    f"Cannot auto-detect MIME type for extension '{ext}'. "
                    "Pass mime_type explicitly."
                )
        logger.info(
            "query_with_file_path | file=%s | mime=%s | model=%s",
            os.path.basename(file_path), mime_type, model_name or self.model_name,
        )
        with open(file_path, "rb") as fh:
            file_data = fh.read()
        return self.query_with_files(
            prompt=prompt,
            file_data=file_data,
            mime_type=mime_type,
            model_name=model_name,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # query_with_multiple_files  (send several files in one request)
    # ------------------------------------------------------------------
    def query_with_multiple_files(
        self,
        prompt: str,
        file_paths: list,
        mime_types: Optional[list] = None,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        Send a prompt with multiple local files in a single request.
        MIME types are auto-detected unless ``mime_types`` list is provided.

        Example:
            response = client.query_with_multiple_files(
                prompt="Compare these two invoices and list the differences.",
                file_paths=["/docs/invoice_jan.pdf", "/docs/invoice_feb.pdf"],
            )
        """
        if mime_types and len(mime_types) != len(file_paths):
            raise ValueError("mime_types list must be the same length as file_paths")
        parts = [types.Part.from_text(text=prompt)]
        for idx, fp in enumerate(file_paths):
            fp = os.path.abspath(fp)
            if not os.path.isfile(fp):
                raise FileNotFoundError(f"File not found: {fp}")
            if mime_types:
                mt = mime_types[idx]
            else:
                ext = os.path.splitext(fp)[1].lower()
                mt = self._MIME_MAP.get(ext)
                if mt is None:
                    raise ValueError(
                        f"Cannot auto-detect MIME type for '{fp}'. "
                        "Pass mime_types explicitly."
                    )
            logger.info("query_with_multiple_files | file[%d]=%s | mime=%s", idx, os.path.basename(fp), mt)
            with open(fp, "rb") as fh:
                parts.append(types.Part.from_bytes(data=fh.read(), mime_type=mt))
        try:
            return self._generate(parts, model_name, "query_with_multiple_files", **kwargs).text
        except Exception as exc:
            raise RuntimeError(f"Failed after {self.max_retries} retries: {exc}") from exc

    # ------------------------------------------------------------------
    # check_connection
    # ------------------------------------------------------------------
    def check_connection(self) -> bool:
        """Credentials refresh and the model is reachable (one metadata read)."""
        try:
            self._ensure_valid_token()
            self.get_model().models.get(model=self.model_name)
            return True
        except Exception as exc:
            logger.error("Connection check failed: %s", exc)
            try:
                self.credentials.refresh(Request())
                self.token_expiry = self.credentials.expiry
                self._connect()
                self.client.models.get(model=self.model_name)
                logger.info("Reconnected successfully after connection check failure")
                return True
            except Exception:
                logger.exception("Failed to reconnect")
                return False


# ---------------------------------------------------------------------------
# GeminiAPILLM  (Gemini Developer API with an API key, not Vertex AI)
# ---------------------------------------------------------------------------
class GeminiAPILLM(VertexAILLM):
    """
    The same client over the Gemini Developer API (aistudio.google.com) with an
    API key instead of a GCP service account on Vertex AI. Every method of
    VertexAILLM works unchanged; there is no token to refresh.

        GEMINI_API_KEY            - the key (required)
        GEMINI_API_PROJECT_NAME   - the key's Google project, for logs only
        GEMINI_API_PROJECT_NUMBER - its number, for logs only
        GEMINI_MODEL_NAME         - default gemini-3-pro-preview
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        max_retries: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout_sec: Optional[float] = None,
        retry_delay: float = 1.0,
        project_name: Optional[str] = None,
        project_number: Optional[str] = None,
    ):
        self.api_key = api_key or _env("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Pass api_key or add it to your .env / .angel.env."
            )
        self.project_id             = project_name or _env("GEMINI_API_PROJECT_NAME")
        self.project_number         = project_number or _env("GEMINI_API_PROJECT_NUMBER")
        self.location               = "gemini-api"
        self.model_name             = model_name or _env("GEMINI_MODEL_NAME", "gemini-3-pro-preview")
        self.max_retries            = _env("VERTEX_MAX_RETRY", 3, int) if max_retries is None else int(max_retries)
        self.temperature            = temperature
        self.timeout_sec            = float(timeout_sec or _env("VERTEX_TIMEOUT_SEC", 120.0, float))
        self.retry_delay            = retry_delay
        self.refresh_before_seconds = 0
        self.service_account_file   = None
        self.credentials  = None
        self.token_expiry = None
        self.client       = None
        self._initialized = False
        self._initialize()

    def _connect(self):
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=int(self.timeout_sec * 1000)),
        )

    def _initialize(self):
        try:
            self._connect()
            logger.info(
                "Initialized Gemini API | project=%s (%s) | model=%s",
                self.project_id, self.project_number, self.model_name,
            )
            self._initialized = True
        except Exception:
            logger.exception("Failed to initialize the Gemini API client")
            raise

    def _ensure_valid_token(self) -> bool:
        return False                    # an API key does not expire

    def _handle_auth_error(self, error: Exception):
        pass                            # nothing to refresh: a refused key stays refused

    def check_connection(self) -> bool:
        """The key is accepted and the model is reachable (one metadata read)."""
        try:
            self.client.models.get(model=self.model_name)
            return True
        except Exception as exc:
            logger.error("Connection check failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Quick smoke-test  (python services/vertex_ai_mime.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    client = VertexAILLM()
    print("Connection OK:", client.check_connection())

    # 1. Plain text query
    result = client.query("Say hello in one sentence.")
    print("\n[query] ->", result)

    # 2. Structured JSON
    # data, meta = client.query_json("Is 7 prime?", schema={"type": "object",
    #     "properties": {"answer": {"type": "boolean"}}, "required": ["answer"]})
    # print("\n[query_json] ->", data, meta)
