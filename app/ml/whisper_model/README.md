Fine-tuned Whisper weights go here.

If this directory is empty at runtime, the STT pipeline loads `openai/whisper-large-v3`.

Expected layout after fine-tuning export:
- `whisper_model/` — checkpoint compatible with `whisper.load_model(path)`
