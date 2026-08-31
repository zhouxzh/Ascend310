# Model asset contract

Only `registry.json` and this document are versioned. Checkpoints, tokenizer
assets, ONNX files, OM files, cloned upstream sources and conversion reports
are generated locally or on the Ascend board and remain ignored by Git.

`candidate_manifest.json` at the case root describes every auditable model
candidate. `models/registry.json` contains only models that passed artifact,
ATC, ACL, numerical and runtime admission gates. The production application
never promotes a model merely because an OM file exists.
