// Option A: neo4j-admin database import full --nodes=nodes.csv --relationships=edges.csv <db>
// Option B: copy nodes.csv/edges.csv into Neo4j's import dir, then run:
CREATE CONSTRAINT kg_id IF NOT EXISTS FOR (n:KG) REQUIRE n.id IS UNIQUE;
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS r
CALL apoc.create.node(['KG', r.`type:LABEL`], {id: r.`id:ID`, name: r.name, modules: r.modules,
  summary: r.summary, formula: r.formula, conditions: r.conditions, params: r.params,
  bot_use: r.bot_use, caveats: r.caveats, facts: r.facts, stub: r.stub}) YIELD node RETURN count(node);
LOAD CSV WITH HEADERS FROM 'file:///edges.csv' AS r
MATCH (a:KG {id: r.`:START_ID`}), (b:KG {id: r.`:END_ID`})
CALL apoc.create.relationship(a, r.`:TYPE`, {modules: r.modules}, b) YIELD rel RETURN count(rel);
// Example: MATCH p=(s:Strategy)-[:USES*1..2]->(i:Indicator) RETURN p LIMIT 50;
