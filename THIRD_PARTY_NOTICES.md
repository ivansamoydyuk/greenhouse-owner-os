# Task audience linguistic data

The task audience preparer performs local dictionary-only lookups. It does not
send source text to a provider. This linguistic evidence never supplies a worker
ID, assignment, dispatch, translation, or owner confirmation.

Pinned packages:

- [pymorphy3 2.0.6](https://pypi.org/project/pymorphy3/2.0.6/), MIT.
- [pymorphy3-dicts-ru 2.4.417150.4580142](https://pypi.org/project/pymorphy3-dicts-ru/2.4.417150.4580142/).
  Python package code: MIT. Dictionary data: OpenCorpora,
  [Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
- [dawg2-python 0.9.0](https://pypi.org/project/dawg2-python/0.9.0/), MIT.
- [setuptools 84.0.0](https://pypi.org/project/setuptools/84.0.0/), MIT;
  required by pymorphy3 on Python 3.12 and later.

Dictionary attribution: [OpenCorpora](https://opencorpora.org/), source version
0.92, revision 417150; compiled dictionary metadata dates the build to
2022-01-08. The application uses the separately installed, unmodified dictionary.
The dependency packages retain their respective license and attribution notices.

Only known dictionary analyses are considered. Prediction, unknown-word suffix
inference, probability ranking and broad named-entity recognition are disabled.
Competing context-compatible common-noun or geographic meanings require audience
clarification. Original spelling and full instructions are retained separately.

For an unknown token, a comma alone, a date, capitalization or a generic
imperative is insufficient. The bounded second-person arrival/movement frame
(e.g. an addressed person told to arrive) may establish recipient discourse.
This is a separate source-discourse inference and never resolves identity.
Unrecognized names outside those frames remain useful audience clarifications.

PyPI release wheel SHA-256:

- pymorphy3: `0254317c02ce3ea17e080b7fc9d675e44662b3a5296bae68605b7a41d25b36c3`
- pymorphy3-dicts-ru: `718bac64c73c10c16073a199402657283d9b64c04188b694f6d3e9b0d85440f4`
