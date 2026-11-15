# UCI Adult (census income)

`adult.data` is the training split of the UCI Adult dataset: 32,561 rows, 15
columns, unchanged from the UCI archive (sha256
`5b00264637dbfec36bdeaab5676b0b309ff9eb788d63554ca0a249491c86603d`).

Becker, B. and Kohavi, R. (1996). *Adult* [Dataset]. UCI Machine Learning
Repository. https://doi.org/10.24432/C5XW20. Licensed under CC BY 4.0.

It stands in for a private dataset in the demo console: the service loads it
into the `adult` table at start-up, and teams query it only through OpenDP
(app/adapters/adult.py). No row is ever returned to a caller.
