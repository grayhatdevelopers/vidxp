# Individual modality gates

Collection index: [Benchmarking research](README.md)

Status: Adapters wired; current-provider dataset runs pending

Last verified: 2026-09-05

These gates answer two different questions before the paid agent comparison:

1. Does the current model and VidXP ranking path work on a task the model was
   designed to perform?
2. Does that output fit VidXP's actual task: finding useful intervals in video?

A native-task pass does not imply a product-task pass. Candidate providers must
use the same dataset, split, query set, and metrics as the current provider.

## Required gates

| Evidence lane | Current provider | Native or isolation gate | Product-task gate | Current state |
| --- | --- | --- | --- | --- |
| Scene | [SigLIP 2](https://arxiv.org/abs/2502.14786) | The existing [DiDeMo](https://github.com/LisaAnne/LocalizingMoments) adapter isolates sampled visual-frame ranking within one video | DiDeMo's fixed five-second moments measure whether those frame scores rank the described visual moment | Adapter complete; one current-provider smoke only |
| Action/video | [VideoPrism LvT](https://arxiv.org/abs/2402.13217) | [MSR-VTT 1K-A](https://github.com/m-bain/frozen-in-time) text-to-video retrieval checks the published global video-text use case and complete-corpus ordering | [Charades-STA](https://github.com/jiyanggao/TALL) checks whether VidXP's independently ranked eight-second action records find labelled action intervals | Both adapters wired; no dataset run |
| Environmental sound | [FineLAP](https://aclanthology.org/2026.acl-long.473/) | Clotho or AudioCaps text-to-audio retrieval checks global clip ranking; TAG checks local phrase-to-frame ordering | [Clotho-Moment](https://h-munakata.github.io/Language-based-Audio-Moment-Retrieval/) or [CASTELLA](https://arxiv.org/abs/2511.15131) checks text-to-interval retrieval over long audio | All three paths wired; no native dataset run |
| Speech meaning | Qwen3 Embedding | HiREST with released transcripts isolates transcript chunking, embedding, and timestamp ranking | The same HiREST known-video moment task scores whether the relevant spoken procedure is localized | Adapter complete; two-pair current-provider smoke only |
| Transcription | faster-whisper | A separate WER run is required on real audio because released-transcript HiREST bypasses transcription | An end-to-end speech run must transcribe media before applying the same retrieval task | Not wired; it does not block ranking-provider comparison but remains required before an ASR claim |

Actor clustering is not part of the current LongVALE-derived agent comparison.
Its BBT/Buffy gate remains blocked on lawful access to the source episodes.

## What the new commands measure

### VideoPrism

`msrvtt-action` indexes every video in the declared 1K-A gallery, searches every
caption, reduces multiple VidXP action records to each video's best-ranked
record, and reports text-to-video R@1/5/10/50, median rank, mean rank, and
mAP@10.
Google's released VideoPrism-LvT-B reports MSR-VTT-1K text-to-video R@1, so this
is the correct provider-level comparison. VidXP's multiple fixed records differ
from Google's single global-video evaluation; the result must therefore state
that representation difference instead of claiming exact leaderboard parity.

`charades-action` searches every action record in the known video and reports
R@1/R@5 at temporal-IoU 0.3/0.5/0.7 plus mean top-one IoU. It tests VidXP's
fixed-window temporal behavior, not VideoPrism's published classification score.

### FineLAP

`finelap-retrieval` accepts FineLAP's official five-caption JSONL format and
queries only its global audio representation. This prevents the earlier error
where global and dense vectors were treated as one calibrated ranking. It
reports the paper's text-to-audio metrics, including R@50. VidXP has no
audio-to-text product operation, so the command does not claim FineLAP's reverse
retrieval score.

`finelap-grounding` accepts FineLAP's published TAG metadata shape, queries only
dense activation records, preserves every labelled occurrence, and reports
ranked temporal-IoU diagnostics. FineLAP's official aggregate uses PSDS and
threshold AUC; VidXP's current command is an ordering diagnostic and must not be
reported as that official score.

`finelap-audio-moment` accepts Lighthouse JSONL records for Clotho-Moment or
CASTELLA and runs VidXP's complete current sound search. This is the relevant
product-fit check. A poor result cannot be dismissed by a good short-clip
retrieval score.

### Existing scene and speech adapters

DiDeMo and HiREST already invoke their pinned official evaluators. Their legacy
full results do not validate the current providers. SigLIP 2 still needs a
declared DiDeMo run, and Qwen3 still needs all 193 HiREST validation pairs.

## Candidate comparison after the current baseline

Do not select a replacement from a LongVALE-derived modality slice. Use the
same frozen gates above:

| Candidate | Run it on | What it can replace if it wins |
| --- | --- | --- |
| [PE-AV](https://huggingface.co/facebook/pe-av-small) | MSR-VTT plus Clotho/AudioCaps, then the temporal product gates | Global VideoPrism and FineLAP retrieval representations; it does not supply interval prediction by itself |
| PE-Video or PE-Core video checkpoints | Do not score as text retrieval without an official paired text head | Video encoders, not established drop-in text-video search providers |
| [PE-A-Frame](https://huggingface.co/facebook/pe-a-frame-small) | TAG or [AEGBench](https://arxiv.org/abs/2607.04383), then Clotho-Moment/CASTELLA | Fine-grained sound localization only |
| [AM-DETR](https://h-munakata.github.io/Language-based-Audio-Moment-Retrieval/) or another audio moment grounder | Clotho-Moment, real UnAV-100, and CASTELLA when available | The current custom long-audio selector |
| A trained video temporal grounder | Charades-STA plus a second-domain temporal set | The current fixed-window action selector, while preserving VidXP's public action API |

PE-A-Frame Small was previously tested on four LongVALE-derived slices. One
reference was effectively silent and another accepted only one of several valid
sound occurrences. It also missed the two unambiguous examples and was slow on
the Mac, so it was not adopted. That diagnostic does not replace TAG, AEGBench,
or an audio-moment benchmark and does not reject PE-AV or PE-Video.

## Run commands

The new adapters use supplied datasets and record input checksums, media/model
identities, query text and ground truth, ranked scores and intervals, timings,
and metrics under `benchmark_runs/`. Dataset downloads remain explicit because
the new sources have separate access and licensing terms.

```bash
vidxp benchmark msrvtt-action \
  --annotations /path/to/MSRVTT_data.json \
  --gallery /path/to/jsfusion_test_ids.txt \
  --media-directory /path/to/msrvtt/videos \
  --run-id current-videoprism

vidxp benchmark charades-action \
  --annotations /path/to/charades_sta_test.txt \
  --media-directory /path/to/charades/videos \
  --run-id current-videoprism

vidxp benchmark finelap-retrieval \
  --metadata /path/to/test_metadata_clotho.jsonl \
  --run-id current-finelap

vidxp benchmark finelap-grounding \
  --metadata /path/to/tag_test.json \
  --audio-directory /path/to/tag/audio \
  --run-id current-finelap

vidxp benchmark finelap-audio-moment \
  --dataset clotho-moment \
  --metadata /path/to/clotho_moment_test.jsonl \
  --audio-directory /path/to/clotho-moment/audio \
  --run-id current-finelap
```

Use the optional subset-index flags only for execution smokes. A subset is never
reported as a provider-quality result. No component result authorizes the paid
MCP-on/MCP-off run; that still requires explicit maintainer confirmation.
