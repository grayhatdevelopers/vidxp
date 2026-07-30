Re-index videos created with the previous provider stack:

- replace WhisperX `large-v2` and `all-MiniLM-L6-v2` with pinned faster-whisper `large-v3-turbo` and Qwen3 Embedding 0.6B for dialogue
- replace OpenAI CLIP `ViT-B/32` with pinned SigLIP2 base patch16-224 for scene retrieval
- replace `face_recognition`/dlib with pinned OpenCV Zoo YuNet and SFace for face detection and within-video grouping
- record model revisions, weight checksums, licenses, precision, runtime identity, and expected download sizes in the index/model contracts

The new embeddings, face thresholds, and provider manifests are intentionally incompatible with old indexes.
