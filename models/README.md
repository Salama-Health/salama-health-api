# Model files

Place the trained ML artifacts here. They are loaded **locally** at startup
(once per worker process) by `app/ml/model_loader.py`.

Expected files (names configurable via env — see `app/config.py`):

| File | Type | Purpose |
|---|---|---|
| `SSD_XGBoost_Flood_Model.pkl` | XGBoost (pickle) | P(flood) — primary |
| `SSD_RF_Model.pkl` | RandomForest (pickle) | P(flood) — ensemble member |
| `SSD_LSTM_Model.pt` | PyTorch (optional) | P(flood) — ensemble member |
| `ensemble_weights.json` | JSON | e.g. `{"xgb":0.341,"rf":0.326,"lstm":0.333}` |

Notes
- Each `.pkl`/`.pt`/`.json` here is git-ignored (see repo `.gitignore`) — ship
  them with the deploy, not the repo.
- The XGBoost and RF models must expose `predict_proba(X)` over the 30-feature
  vector defined in `app/ml/features.py` (feature order matters).
- If a file is missing the loader logs a warning and falls back gracefully
  (rule-based flood heuristic). Set `REQUIRE_MODELS=true` to fail startup instead.
- The LSTM only loads if `torch` is installed (it's commented out in
  `requirements.txt` to keep the image small).

## Optional: load from Hugging Face Hub instead

Set `HF_MODEL_REPO=mubarakabanadda/salama-cdi-models` and the loader will
download any missing file from that repo into `models/.hf_cache/`.
