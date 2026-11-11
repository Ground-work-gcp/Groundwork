"""Rural land records (India): make land that is hard to read sellable, inheritable and bankable.

Modules, each a pure function of the case's records except ``service`` and ``agent``:

* ``regions``     — record names, offices, units, fees and lender checklists per state
* ``units``       — extents across acres/cents/guntas/hectare-are/bigha; survey-number keys; land classes
* ``names``       — one person's name across Tamil/Devanagari/Kannada/… scripts and spellings
* ``parse``       — patta, adangal, FMB, EC, sale deed and certificates out of uploaded documents
* ``concordance`` — where the records disagree (names, extents, survey numbers, boundaries, class), and who corrects it
* ``succession``  — draft shares through every death since the record was last changed
* ``valuation``   — a conservative range with confidence; a lending value below the median
* ``readiness``   — what each lender asks for, what is missing, and a critical-path fix plan
* ``money``       — what the family spends in rupees and days, receipted or not, and the informal-credit premium
* ``explain``     — the same picture in plain English, Tamil or Hindi
* ``documents``   — the mutation packet, co-heir consent letters, and the land credit passport
* ``agent``       — an audited agent that runs all of the above and hands off what only people can do

Boundaries: Groundwork prepares the case. Ownership decisions stay with revenue authorities and
courts; every valuation carries a stated range and confidence.
"""
