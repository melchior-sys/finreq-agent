"""Retrieval queries held out of floor calibration.

Written against the SON corpus without looking at the calibration set in
`calibrate_retrieval.py` or at the floor it produced. They are never used to choose
the threshold — only to report how it behaves on queries it was not fitted to.

This separation is the whole point, so it is structural: a different file, imported
only by the reporting path. The numbers on the calibration page are training
accuracy; these are the ones to quote.
"""

from __future__ import annotations

# Phrased as a client or a colleague would ask, and deliberately not reusing the
# vocabulary of the calibration queries.
RELEVANT_HELD_OUT: list[tuple[str, str]] = [
    ("are reversals ever shown to the customer", "SON-001#3.4"),
    ("do we print holds that have not completed yet", "SON-001#3.3"),
    ("how long before an unmatched hold is released", "SON-001#2"),
    ("how many lines fit on one printed page", "SON-001#3.5"),
    ("can we change statements that have already gone out", "SON-001#5"),
    ("what counts as a qualifying salary payment", "SON-002#2"),
    ("is there a cap on how many fees we can waive", "SON-002#3.4"),
    ("cost of taking cash out at another provider machine", "SON-002#3.3"),
    ("which day of the month does the period end", "SON-003#3.1"),
    ("what happens when the closing date is a weekend", "SON-003#3.3"),
]

# Near-domain retail banking, none of it covered by these three documents. An easy
# negative set would flatter the floor.
IRRELEVANT_HELD_OUT: list[str] = [
    "how do I dispute a direct debit",
    "what is the cashback rate on this card",
    "set up a standing order to my landlord",
    "why was my card declined abroad",
    "close my account and transfer the balance out",
    "what is the exchange rate for euros today",
    "book an appointment with a mortgage adviser",
    "how do I turn on dark mode in the app",
    "generate a quarterly VAT return",
    "what is the best way to invest a lump sum",
]
