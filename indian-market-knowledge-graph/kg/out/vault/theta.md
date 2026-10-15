---
id: theta
type: Greek
modules: [M05]
tags: [greek]
---
# Theta (Time Decay)

Premium lost per day from passage of time, all else equal; options are depreciating assets. Long options = negative theta; short options = positive theta (friend of sellers).

## Formula
- **premium_tomorrow**: `premium_today - theta`

## Facts
- Time decay is slow early in the series and accelerates sharply near expiry.
- Selling early collects more time value but decays slowly; selling near expiry collects less but decays fast.

## Example
Short at 54 with theta 0.75 -> ~51.75 after 3 days.

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[option_premium_decomposition]]

## Backlinks
- [[option_greeks]] PART_OF →
- [[gamma]] RELATED_TO →
- [[sd_option_writing]] USES →
- [[strike_selection]] USES →
