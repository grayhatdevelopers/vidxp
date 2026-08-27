# Published comparison results

Collection index: [Benchmarking research](README.md)

Status: Primary-source result extraction complete

Last verified: 2026-08-27

This is the answer to “what did the published competitors actually score?” It is
the result-level companion to the capability matrix in the
[benchmark catalog](benchmark_catalog.md). Current VidXP measurements and direct
comparisons are in [results](results.md). This document does not turn scores from
different splits or task definitions into one leaderboard.

## Evidence and comparison rules

Every numeric row below must name:

- the exact benchmark task and split;
- whether the method is frozen/zero-shot, trained on the benchmark, or otherwise
  supervised;
- the metric and its published scale;
- the primary paper and exact table/page;
- an official code, model, data, or evaluator link when one was found.

“Direct after a VidXP run” means the number may be compared only after VidXP uses
the same corpus, split, prediction unit, and evaluator. “Context only” means the
published method used task training, unavailable inputs, a different split, or a
different protocol. A missing artifact is stated; it is never silently replaced
with a third-party implementation.

Scores are preserved on the authors' scale. In particular, FLARE, LongVALE,
VALOR-32K, CLaMR, MUVR, and several temporal-retrieval papers report percentages,
while MultiVENT 2.0 and TRECVID report fractions in `[0, 1]`.

## Component-model selection results

These results choose first integration candidates. They are not VidXP scores and
do not remove the need for bounded repository-specific validation after a provider
exists.

### FineLAP: audio-text retrieval and dense sound features

Source: [ACL 2026 paper](https://aclanthology.org/2026.acl-long.473/), Table 2,
proceedings pp. 10398–10399.

| Model | AudioCaps text→audio R@1 | AudioCaps audio→text R@1 | Selection use |
| --- | ---: | ---: | --- |
| FineLAP | 45.7 | 62.5 | First environmental-sound provider; also exposes dense frame features |
| LAION-CLAP | 35.1 | 44.2 | Mature native-Transformers integration baseline |

The same paper uses fixed ten-second FineLAP inputs and identifies variable-length
audio as future work. Long-media integration therefore still needs timestamped
windowing, overlap, and span merging owned by VidXP.

### MVEB: current text-video embedding comparison

Source: [MVEB paper](https://arxiv.org/pdf/2606.14958), Table 11, PDF p. 30.

| Model | MVEB(text, video) mean | Selection use |
| --- | ---: | --- |
| Qwen3-VL-Embedding-8B | 60.9 | Published quality ceiling |
| Qwen3-VL-Embedding-2B | 58.1 | Practical first visual scene/action candidate |
| LCO-Embedding-Omni-7B | 56.8 | Strong audio-video-text context, but substantially larger |

The checked MVEB paper contains no VideoPrism row. These numbers support the Qwen
candidate order, but they do not establish a direct Qwen-versus-VideoPrism win.
VideoPrism remains an incumbent multi-frame encoder control.

### TimeLens2: visual temporal grounding size trade-off

Source: [official TimeLens2 release table](https://github.com/MCG-NJU/TimeLens2),
checked 2026-08-27.

| Checkpoint | Seven-dataset average mIoU | Selection use |
| --- | ---: | --- |
| TimeLens2-4B | 47.7 | First visual temporal-grounding candidate after cheap recall |
| TimeLens2-8B | 48.0 | Quality ceiling; not the practical default for a 0.3-point gain |

Both checkpoints are visual-only. Neither evaluates environmental audio or spoken
content, so neither can cover LongVALE's full task alone.

### AEGBench: open-vocabulary sound boundaries

Source: [Auto-AEG/AEGBench](https://arxiv.org/html/2607.04383v4), Table 3.

| Model | mIoU | Event F1 | Segment F1 | Selection use |
| --- | ---: | ---: | ---: | --- |
| PE-A-Frame Large | 0.389 | 0.407 | 0.607 | Released sound-localization specialist and integration comparator |
| DASM | 0.204 | 0.215 | 0.277 | Lower detector baseline |
| Qwen3-Omni-30B + SFT + GRPO | 0.480 | 0.524 | 0.697 | Training-heavy research ceiling, not the local first pick |

AEGBench contains 3,427 items and 9,790 queries with multiple hard-case labels,
including repeated occurrence, polyphonic overlap, gradual boundaries, and long
duration. It is a component benchmark, not evidence of end-to-end visual/sound/
speech fusion.

## Whole-system and multimodal retrieval

### LongVALE: known-video omni-modal temporal grounding

Sources: [CVPR 2025 paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Geng_LongVALE_Vision-Audio-Language-Event_Benchmark_Towards_Time-Aware_Omni-Modal_Perception_of_Long_Videos_CVPR_2025_paper.pdf),
[official repository](https://github.com/ttgeng233/LongVALE), and
[dataset](https://huggingface.co/datasets/ttgeng233/LongVALE).

The closest task is Omni-TVG on the LongVALE test set. Existing public Video LLM
checkpoints were evaluated by the LongVALE authors; the paper does not describe
LongVALE tuning for them. LongVALE-LLM was trained with LongVALE
boundary-perception and instruction data. The paper samples vision, generic
audio, and speech, so a VidXP vision+speech run would remain a declared
partial-modality system.

| Method | LongVALE training | Input/temporal support | R@1 IoU .3 | R@1 IoU .5 | R@1 IoU .7 | mIoU | Comparison use | Evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| VideoLLaMA 7B | No | Audio+video; no fine temporal support | 2.5 | 1.1 | 0.3 | 1.9 | Frozen context | Table 2, PDF p. 7 |
| TimeChat 7B | No | Visual; temporal support | 5.8 | 2.6 | 1.1 | 5.2 | Frozen visual-temporal context | Table 2, PDF p. 7 |
| VTimeLLM 7B | No | Visual; temporal support | 7.5 | 3.4 | 1.3 | 6.4 | Frozen visual-temporal context | Table 2, PDF p. 7 |
| LongVALE-LLM 7B | Yes | Audio+video; temporal support | 15.7 | 8.6 | 3.9 | 11.0 | Trained upper context | Table 2, PDF p. 7 |

Do not describe the first three rows as generic zero-shot temporal-retrieval
standards outside the authors' LongVALE evaluation setup. Do not compare VidXP
to LongVALE-LLM without labeling the latter as trained on the target data.

### FLARE: long-video audiovisual clip retrieval

Sources: [arXiv v1 paper](https://arxiv.org/pdf/2605.10228),
[official benchmark site](https://flarebench.github.io/),
[code](https://github.com/YqjMartin/FLARE), and
[data](https://huggingface.co/datasets/YqjMartin/FLARE).

FLARE evaluates fixed public checkpoints without task training. Caption-based
evaluation supports text-to-clip, text-to-video, and reverse directions;
generated-query evaluation is clip-level only. The query set is model-generated,
relevance-filtered, and rank-1 validated. FLARE was an unreviewed May 2026
preprint at verification time.

| Regime | Method/media | Text→clip R@1 / R@5 / R@10 | Text→video R@1 / R@5 / R@10 | Comparison use | Evidence |
| --- | --- | --- | --- | --- | --- |
| Caption | CLIP ViT-B/32, vision | 7.98 / 18.92 / 25.38 | 24.06 / 44.36 / 53.38 | Direct frozen vision baseline | Table 2, PDF p. 8 |
| Caption | ImageBind, vision+audio | 7.64 / 18.66 / 25.21 | 35.33 / 61.65 / 71.42 | Direct frozen joint-embedding baseline | Table 2, PDF p. 8 |
| Caption | LanguageBind, vision+audio | 2.70 / 7.15 / 10.23 | 23.80 / 48.37 / 58.14 | Direct frozen joint-embedding baseline | Table 2, PDF p. 8 |
| Caption | Wave-7B, vision+audio | 65.51 / 83.50 / 88.26 | 91.23 / 99.75 / 100.0 | Strong fixed-checkpoint context | Table 2, PDF p. 8 |
| Generated query | CLIP ViT-B/32, vision | 13.89 / 29.01 / 36.82 | Not evaluated | Direct frozen vision baseline | Table 3, PDF p. 9 |
| Generated query | ImageBind, vision+audio | 6.35 / 16.59 / 23.09 | Not evaluated | Direct frozen joint-embedding baseline | Table 3, PDF p. 9 |
| Generated query | LanguageBind, vision+audio | 3.32 / 8.98 / 12.72 | Not evaluated | Direct frozen joint-embedding baseline | Table 3, PDF p. 9 |
| Generated query | Wave-7B, vision+audio | 42.63 / 67.63 / 76.26 | Not evaluated | Strong fixed-checkpoint context | Table 3, PDF p. 9 |

The authors' media ablation is a useful warning for VidXP's fixed fusion:
ImageBind query Text→Clip R@1 is 6.35 with the full embedding but 12.87 with
vision-only media; LanguageBind is 3.32 full versus 15.52 vision-only. Those
values are from Table 4 (PDF p. 16), not a separate test split.

### MultiVENT 2.0: original benchmark baselines

Sources: [CVPR 2025 paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Kriz_MultiVENT_2.0_A_Massive_Multilingual_Benchmark_for_Event-Centric_Video_Retrieval_CVPR_2025_paper.pdf)
and [official data/evaluator](https://huggingface.co/datasets/hltcoe/MultiVENT2.0).

These are the defining paper's pre-trained/pipeline baselines. MultiVENT 2.0 is
whole-video event retrieval, not temporal localization. Its four evidence
channels are vision, ASR speech, OCR embedded text, and human description
metadata; it does not define generic acoustic-event retrieval.

| Method/channel | R@10 | R@100 | MRR | mAP | nDCG@10 | Comparison use | Evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| mCLIP, vision | 0.333 | 0.603 | 0.429 | 0.261 | 0.303 | Direct frozen visual baseline | Table 3, PDF p. 7 |
| ICDAR → mCLIP, OCR | 0.227 | 0.374 | 0.363 | 0.166 | 0.217 | Unsupported-channel context | Table 3, PDF p. 7 |
| Whisper → mCLIP, speech | 0.290 | 0.450 | 0.417 | 0.212 | 0.267 | Closest frozen ASR pipeline baseline | Table 3, PDF p. 7 |
| Description → mCLIP, metadata | 0.293 | 0.491 | 0.445 | 0.228 | 0.284 | Oracle/metadata context | Table 3, PDF p. 7 |
| InternVideo2.0, vision | 0.004 | 0.018 | 0.018 | 0.003 | 0.005 | Frozen model context | Table 3, PDF p. 7 |
| VAST, vision-only run | 0.118 | 0.118 | 0.198 | 0.080 | 0.116 | Frozen model context | Table 3, PDF p. 7 |
| LanguageBind, all | 0.355 | 0.620 | 0.443 | 0.283 | 0.324 | Closest frozen all-modality baseline | Table 3, PDF p. 7 |

The defining paper reports only 39% Judged@10. Any later system comparison must
use the same qrels and rule for unjudged documents.

### MultiVENT 2.0: later full-test competitors

MMMORRF is a component pipeline structurally close to VidXP: frame retrieval plus
ASR/OCR text retrieval and fixed rank fusion. Sources:
[SIGIR 2025 paper/DOI](https://doi.org/10.1145/3726302.3730157),
[author arXiv copy](https://arxiv.org/pdf/2503.20698), and
[official demo repository](https://github.com/hltcoe/video-retrieval-demo).

| MMMORRF Table 1 method | Channels/fusion | nDCG@10 | R@10 | Comparison use |
| --- | --- | ---: | ---: | --- |
| LanguageBind | Unified embedding | 0.324 | 0.355 | Frozen baseline |
| SigLIP | Frames | 0.375 | 0.409 | Frozen visual baseline |
| PLAID-X | ASR | 0.427 | 0.425 | Extracted-speech baseline |
| PLAID-X joint index | OCR+ASR | 0.551 | 0.556 | OCR-inclusive context |
| SigLIP + PLAID-X JI + RRF | Vision+OCR+ASR, fixed fusion | 0.562 | 0.600 | Closest fixed-fusion comparator |
| SigLIP + PLAID-X JI + WRRF | Vision+OCR+ASR, video-weighted fixed fusion | 0.586 | 0.611 | Closest enhanced-fusion comparator |

All values above are from Table 1, PDF p. 4. MMMORRF uses the validation split
for its TVR experiment but the MultiVENT 2.0 test collection for this block.

OmniEmbed-MultiVENT reports the official MAGMaR shared-task test. Sources:
[system paper](https://arxiv.org/pdf/2506.09409),
[released model checkpoint](https://huggingface.co/Tevatron/OmniEmbed-v0.1-multivent),
the [MAGMaR proceedings introduction](https://aclanthology.org/2025.magmar-1.0.pdf),
and [shared-task evaluation page](https://eval.ai/web/challenges/challenge-page/2507).

| OmniEmbed Table 1 method | Training/input | nDCG@10 | R@10 | Comparison use |
| --- | --- | ---: | ---: | --- |
| DRAMA-1B | Zero-shot `text*`: title+generated caption+human description+Whisper ASR | 0.629 | 0.649 | Oracle/metadata-inclusive context |
| OmniEmbed | Zero-shot `text*`+video+audio | 0.595 | 0.616 | Oracle/metadata-inclusive fixed-checkpoint context |
| OmniEmbed-MultiVENT | Target-trained video+audio | 0.709 | 0.724 | Target-trained non-oracle upper context |
| OmniEmbed-MultiVENT | Target-trained `text*`+video+audio | 0.753 | Conflicting: 0.715 in Table 1; 0.769 in Table 2 | Target-trained all-input/oracle context |

nDCG@10 values and the first three R@10 values are from Table 1, PDF p. 3.
The paper is internally inconsistent for the all-input run: Table 1 reports
R@10 `0.715` and AP `0.769`, while Table 2 reports R@10 `0.769` and AP `0.715`.
The inconsistency is preserved rather than silently choosing one. The MAGMaR
introduction identifies `0.709` as the best non-oracle score; the `0.753`
all-input run must not be used as the primary raw-content comparator.

### CLaMR: public 1,504-query MultiVENT evaluation

Sources: [arXiv paper](https://arxiv.org/pdf/2506.06144) and
[official repository](https://github.com/meetdavidwan/clamr).

CLaMR trains on 367,644 synthetic MultiVENT 2.0++ queries but evaluates on the
original human-judged public evaluation of 1,504 queries. This is not the full
2,549-query shared-task test above.

| Method | Training/status | R@1 | R@5 | R@10 | nDCG@10 | Comparison use |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Video + mCLIP | Off-the-shelf vision | 10.1 | 35.9 | 45.7 | 26.8 | Frozen visual baseline |
| Whisper + mCLIP | Off-the-shelf ASR text | 4.5 | 19.7 | 24.5 | 13.9 | Frozen speech pipeline |
| mCLIP average | Off-the-shelf all-channel score average | 7.9 | 31.9 | 39.7 | 23.0 | Closest naive-fusion baseline |
| ImageBind, vision | Off-the-shelf vision | 15.4 | 43.0 | 52.1 | 32.8 | Frozen visual baseline |
| LanguageBind, vision | Off-the-shelf vision | 14.2 | 39.5 | 47.9 | 30.2 | Frozen visual baseline |
| Qwen-VL 2.5 pooled | Target-trained contrastive baseline | 21.6 | 74.8 | 81.6 | 52.2 | Trained context |
| CLaMR VLM | Synthetic target-trained | 26.7 | 85.1 | 88.0 | 58.5 | Trained upper context |

All values are from Table 1, PDF p. 7. The paper labels this column
“MultiVENT 2.0++,” but its dataset paragraph explicitly says the test is the
original public MultiVENT 2.0 evaluation; “2.0++” is the synthetic training
augmentation, not a newly judged test set.

### Original MultiVENT: Q2E zero-shot fusion

Q2E uses the earlier 2,394-video/259-query MultiVENT dataset, not MultiVENT 2.0.
It is relevant because it combines visual descriptions and Whisper ASR using
training-free rank fusion. Sources:
[AACL 2025 paper](https://aclanthology.org/2025.ijcnlp-long.121.pdf),
[project page](https://dipta007.github.io/Q2E/), and
[official code](https://github.com/dipta007/Q2E).

| Method | Input | R@10 | MRR | nDCG | mAP | Comparison use | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| MultiCLIP | Vision | 70.82 | 0.92 | 75.34 | 86.33 | Frozen baseline | Table 15, PDF p. 18 |
| Q2E | Vision-derived captions and query decomposition | 75.76 | 0.95 | 80.04 | 89.42 | Training-free enhanced visual context | Table 15, PDF p. 18 |
| Q2E + ASR | Above plus Whisper transcript | 79.60 | 0.95 | 83.24 | 91.20 | Closest training-free visual+speech fusion context | Table 15, PDF p. 18 |

These percentages cannot be placed beside MultiVENT 2.0 fractions: the corpus,
query set, relevance structure, and metric implementation differ.

### VALOR-32K: audiovisual whole-video retrieval

Sources: [TPAMI/arXiv paper](https://arxiv.org/pdf/2304.08345) and
[official repository](https://github.com/TXH-mercury/VALOR).
The 25K/3.5K/3.5K split contains ten-second AudioSet-derived clips and human
audiovisual captions. The authors reran the non-VALOR methods on VALOR-32K using
their public code.

| Method | Pretraining/input | R@1 | R@5 | R@10 | Comparison use | Evidence |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Frozen | 6.1M pretraining; VALOR-32K downstream training; vision | 32.9 | 60.4 | 71.2 | Target-trained/pretrained visual context | Table 3, PDF p. 9 |
| AVLNet | 136M pretraining; VALOR-32K downstream training; vision+audio | 21.6 | 47.2 | 59.8 | Target-trained/pretrained audiovisual context | Table 3, PDF p. 9 |
| CLIP4Clip | Vision | 43.4 | 69.9 | 79.7 | Trained visual context | Table 3, PDF p. 9 |
| VALOR-B-minus | 5.5M examples; vision | 43.3 | 70.3 | 80.0 | Authors' visual ablation | Table 3, PDF p. 9 |
| VALOR-B | 6.5M examples; vision+audio | 67.9 | 89.7 | 94.4 | Trained audiovisual context | Table 3, PDF p. 9 |
| VALOR-L | 33.5M examples; vision+audio | 73.2 | 91.6 | 95.4 | Trained upper context | Table 3, PDF p. 9 |
| VALOR-L + dual softmax | 33.5M examples; vision+audio | 80.9 | 93.9 | 97.1 | Trained/post-processed upper context | Table 3, PDF p. 9 |

None of these is a frozen VidXP-like result. They become context for the same
VALOR-32K evaluator, not evidence that a target-trained model and VidXP had equal
training conditions.

### MUVR: paired video+text whole-video retrieval

Sources: [NeurIPS 2025 paper](https://papers.neurips.cc/paper_files/paper/2025/hash/2a80c10b1fd6a6488a96cc1f4fbacc84-Abstract-Datasets_and_Benchmarks_Track.html),
[official repository](https://github.com/debby-0527/MUVR), and
[dataset](https://huggingface.co/datasets/debby0527/MUVR).

MUVR's defining query is a query video plus detailed text. Pure-text and
pure-video are ablations. The rows below are from MUVR-Base and use fixed
pretrained feature models; the paper does not describe MUVR target training for
these rows.

| Method/query form | Training/status | mAP | uAP | R@200 | R@500 | R@2000 | Comparison use | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| EVA-CLIP, pure text | Fixed checkpoint; no MUVR training described | 43.0 | 23.3 | 61.9 | 73.3 | 86.9 | Text-ablation context | Table 7, PDF p. 8 |
| EVA-CLIP, pure video | Fixed checkpoint; no MUVR training described | 50.7 | 33.1 | 66.6 | 77.8 | 90.5 | Reusable visual slice | Table 7, PDF p. 8 |
| InternVideo2, pure video | Fixed checkpoint; no MUVR training described | 48.0 | 36.9 | 62.9 | 75.2 | 89.4 | Reusable visual slice | Table 7, PDF p. 8 |
| EVA-CLIP, multimodal query | Fixed checkpoint; no MUVR training described | 58.0 | 44.6 | 73.0 | 82.5 | 92.3 | Defining paired-query baseline | Table 7, PDF p. 8 |
| InternVideo2, multimodal query | Fixed checkpoint; no MUVR training described | 52.1 | 37.4 | 66.9 | 78.4 | 90.7 | Defining paired-query baseline | Table 7, PDF p. 8 |

VidXP cannot claim the defining MUVR task from text-only search. A pure-video or
pure-text run must be labeled as the corresponding paper ablation.

### VRAgent: zero-shot multimodal whole-video retrieval

Source: [WACV 2026 paper](https://openaccess.thecvf.com/content/WACV2026/papers/Shah_VRAgent_Self-Refining_Agent_for_Zero-Shot_Multimodal_Video_Retrieval_WACV_2026_paper.pdf).
All rows are test-time/zero-shot. MM-MSRVTT queries were generated with GPT-4o
from frames and ASR. For TVR-1200, the authors select one query per video, trim
each video to that query's ground-truth moment, and perform whole-item retrieval
over the 1,200 trimmed clips; this is not original TVR full-video or temporal
retrieval. Scores are `R@1/R@5/R@10 | average recall`.

| Dataset/method | Published result | Comparison use |
| --- | --- | --- |
| MM-MSRVTT, VISPROG | 38.7/71.4/78.3 \| 62.8 | Zero-shot agent baseline |
| MM-MSRVTT, VAST | 39.6/69.0/76.0 \| 61.5 | Zero-shot multimodal context |
| MM-MSRVTT, ASR tool | 19.6/34.4/39.8 \| 31.3 | Transcript-only control |
| MM-MSRVTT, Ensemble (RRF) | 19.8/64.0/80.8 \| 54.9 | BLIP-2+InternVideo2+ASR fixed-fusion control |
| MM-MSRVTT, VRAgent | 50.4/76.4/86.6 \| 71.1 | Defining zero-shot result |
| TVR-1200, VISPROG | 17.9/36.4/47.0 \| 33.8 | Zero-shot agent baseline |
| TVR-1200, ASR tool | 9.2/21.3/31.3 \| 20.6 | Transcript-only control |
| TVR-1200, CLIP | 14.8/32.6/42.3 \| 29.9 | Frozen visual control |
| TVR-1200, VRAgent | 20.2/38.2/48.0 \| 35.5 | Defining zero-shot result |

No public query annotations, evaluator, predictions, or implementation were
found. These are useful citations but cannot be independently reproduced from
the paper alone.

### ContextIQ: expert-fusion precision on a small custom slice

Sources: [WACV 2025 paper](https://openaccess.thecvf.com/content/WACV2025/html/Chaubey_ContextIQ_A_Multimodal_Expert-Based_Video_Retrieval_System_for_Contextual_Advertising_WACV_2025_paper.html)
and [supplemental annotations](https://github.com/AnokiAI/ContextIQ-Paper).
Table 3 evaluates 500 validation clips and eight concepts. Each vector is
`P@5/10/15/20/25/30/35/40/45/50`.

| Method | Precision vector |
| --- | --- |
| CLIP-large | 100/100/99.2/98.8/98.5/98.8/96.8/95.0/93.3/90.3 |
| LanguageBind | 100/98.8/98.3/98.1/98.5/98.8/97.1/95.9/94.7/92.0 |
| OnePeace | 92.5/91.3/92.5/94.4/93.5/93.3/93.2/92.5/90.8/90.3 |
| ContextIQ | 100/100/100/99.4/99.0/98.8/98.2/98.1/97.2/97.3 |

The repository supplies metadata/annotations, not the implementation,
evaluator, or media. Eight multilabel queries are too narrow for a general
retrieval claim. The paper's separate MSR-VTT `P@1/P@5/R@5/custom-mAP@5`
numbers—LanguageBind `85.5/66.6/97.7/86.6` and ContextIQ
`81.7/59.1/93.7/83.2`—use nonstandard metrics and must not be mixed with
canonical MSR-VTT recall.

### Multi-modal Video Search by Examples: closest private system result

Source: [IET Computer Vision 2024 paper](https://pure.ulster.ac.uk/ws/files/222412425/IET_Computer_Vision_-_2024_-_Wu_-_Multi_modal_video_search_by_examples_A_video_quality_impact_analysis.pdf).
On the authors' proprietary high-quality BBC corpus, Table 9 reports
query-by-example fusion for five politician face/speaker queries:

| Fusion | P@1 | P@5 | P@10 | P@50 | P@200 | Recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| combSUM | 1.000 | 0.920 | 0.880 | 0.488 | 0.177 | 0.744 |
| Reciprocal-rank fusion | 1.000 | 0.840 | 0.800 | 0.488 | 0.177 | 0.744 |

The corresponding median/mean ranks are `18.4/21.6` for combSUM and
`19.3/21.9` for reciprocal-rank fusion.

This is the closest functional system comparator, but it measures private
query-by-example face/speaker retrieval—not open text-to-scene search—and the
12,576-video BBC corpus and relevance judgments are not portable. It is
citation context, not a score VidXP can currently reproduce.

### Collaborative Experts and MMT: multi-expert whole-video context

Sources: [corrected Collaborative Experts paper](https://arxiv.org/pdf/1907.13487),
[official CE repository](https://github.com/albanie/collaborative-experts),
[MMT paper](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123490205.pdf),
and [MMT repository](https://github.com/gabeur/mmt).
On MSR-VTT 1K-A, target-trained CE reports text-to-video
`20.9±1.2/48.8±0.6/62.4±0.8` and video-to-text
`20.6±0.6/50.3±0.5/64.0±0.2` at `R@1/5/10`.
The corrected paper's full-split expert ablation grows from scene-only
`4.0/14.1/22.4` to scene+speech `4.6/15.5/24.4`, scene+speech+audio
`5.8/19.0/28.8`, then all experts `10.0/29.0/41.2`. Use the corrected v2
numbers, not the superseded original.

MMT reports target-trained-from-scratch `R@5 54.0, MedR 4, MnR 26.7`;
HowTo100M-pretrained plus target-trained `57.1, 4, 24.0`; and zero-shot
`14.4, 66, 148.1`. Its repository checkpoint expectation
`R@1/5/10 24.1/56.4/69.6` is not the paper's scratch row and must be labeled
as a separate released-checkpoint result.

### SAVE/EclipSE audiovisual family: trained ceilings, not frozen peers

Sources: [SAVE CVPR 2026 paper](https://openaccess.thecvf.com/content/CVPR2026/papers/Zhao_SAVE_Speech-Aware_Video_Representation_Learning_for_Video-Text_Retrieval_CVPR_2026_paper.pdf),
[official SAVE repository](https://github.com/ruc-aimc-lab/SAVE),
[AVIGATE repository](https://github.com/BoseungJeong/AVIGATE-CVPR2025),
[EclipSE repository](https://github.com/GenjiB/ECLIPSE), and
[TEFAL paper](https://openaccess.thecvf.com/content/ICCV2023/papers/Ibrahimi_Audio-Enhanced_Text-to-Video_Retrieval_using_Text-Conditioned_Feature_Alignment_ICCV_2023_paper.pdf).
Table 3 scores are `R@1/R@5/R@10 | Rsum`; every model is trained for the target
dataset. SAVE is the only row with a dedicated speech/ASR branch; EclipSE,
TEFAL, and AVIGATE use generic audio.

| Dataset | EclipSE | TEFAL | AVIGATE | SAVE |
| --- | --- | --- | --- | --- |
| MSR-VTT-9K | 44.9/71.3/81.6 \| 197.8 | 49.4/75.9/83.9 \| 209.2 | 50.2/74.3/83.2 \| 207.7 | 51.3/78.0/86.9 \| 216.2 |
| MSR-VTT-7K | 30.2/55.9/66.6 \| 152.7 | 32.6/59.1/69.3 \| 161.0 | 32.7/59.8/70.2 \| 162.7 | 33.5/60.9/71.4 \| 165.8 |
| VATEX | 57.8/88.4/94.3 \| 240.5 | 61.0/90.4/95.3 \| 246.7 | 63.1/90.7/95.5 \| 249.3 | 66.1/92.6/96.8 \| 255.5 |
| Charades | R@1 15.7 only | 18.5/37.3/48.6 \| 104.4 | 18.8/40.0/51.8 \| 110.6 | 20.8/44.7/55.9 \| 121.4 |
| LSMDC | 22.2/43.8/52.9 \| 118.9 | 24.7/45.1/53.7 \| 123.5 | 24.6/46.0/55.1 \| 125.7 | 26.1/46.4/55.8 \| 128.3 |

These are whole-video or pre-segmented-clip retrieval scores, not boundary
localization. No official TEFAL implementation was found, and no SAVE checkpoint
was visible in the audited repository. SAVE's reported query-independent
retrieval latency (`9.90 ms`) must not be compared directly with TEFAL's
query-conditioned `140.57 ms` without matching hardware and corpus setup.

### TRECVID 2024 AVS: submitted runs, not portable model names

Sources: [official TRECVID 2024 overview](https://trec.nist.gov/pubs/trec33/papers/Overview_avs_vtt_actev.pdf),
[AVS run appendix and participant reports](https://trec.nist.gov/pubs/trec33/appendices/trec2024-avs-main.html),
and [2024 AVS guidelines](https://www-nlpir.nist.gov/projects/tv2024/avs.html).
The values are pooled-shot challenge results over that year's 20 main queries.
The run IDs identify submissions; their method details live in separate team
reports.

| Run category | Published reference | Mean xinfAP | Evidence |
| --- | --- | ---: | --- |
| Fully automatic, best | `F_D_C_D_NII_UIT.24_1` | 0.425 | Table 2, PDF p. 5 |
| Fully automatic, reported median | All 29 automatic runs | 0.314 | Results text, PDF p. 5 |
| Manually assisted, best | `M_D_C_D_NII_UIT.24_2` | 0.422 | Table 3, PDF p. 7 |
| Relevance feedback, best | `R_D_C_D_WHU-NERCMS.24_2` | 0.344 | Table 4, PDF p. 7 |

Only the fully automatic category is a plausible VidXP result context. A new run
also needs the exact 2024 topics, qrels, sampling plan, and `sample_eval` tool;
the headline number alone is not reproducible evidence.

## Dialogue, transcript, and corpus-moment retrieval

These benchmarks are the closest published evidence for VidXP's transcript and
speech-backed retrieval path. They do not all measure the same capability:
TVR and HERO retrieve moments from a corpus, HiREST separates whole-video
retrieval from known-video moment localization, TVR-Ranking grades several
relevant moments, and QuerYD's published segment task ranks supplied oracle
proposals.

### TVR / XML: video-plus-subtitle corpus moment retrieval

Sources: [ECCV 2020 paper](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123660443.pdf),
[official repository](https://github.com/jayleicn/TVRetrieval), and
[standalone evaluator](https://github.com/jayleicn/TVRetrieval/tree/master/standalone_eval).

The primary VCMR comparison below uses the 5,445-query/1,089-video TVR
`test-public` set and both video and supplied subtitle features. A prediction is
correct only when it selects the correct video and an interval meeting the
stated tIoU. The published table does not break these rows into the
`video-only`, `sub-only`, and `video+sub` query types. Therefore it is
combined-modality context, not evidence for a transcript-only VidXP run.
Supplied subtitles also bypass WhisperX.

| Method | Training/input | R@1 / R@10 at tIoU .5 | R@1 / R@10 at tIoU .7 | Comparison use | Evidence |
| --- | --- | --- | --- | --- | --- |
| MCN | TVR-trained; video+subtitle | 0.02 / 0.24 | 0.00 / 0.09 | Proposal baseline context | Table 5, PDF p. 19 |
| CAL | TVR-trained; video+subtitle | 0.09 / 0.57 | 0.04 / 0.26 | Proposal baseline context | Table 5, PDF p. 19 |
| MEE + MCN | TVR-trained retrieval and reranking; video+subtitle | 0.92 / 5.58 | 0.42 / 2.98 | Trained pipeline context | Table 5, PDF p. 19 |
| MEE + CAL | TVR-trained retrieval and reranking; video+subtitle | 0.97 / 5.80 | 0.39 / 2.98 | Trained pipeline context | Table 5, PDF p. 19 |
| XML | TVR-trained; video+subtitle | 7.25 / 21.65 | 3.25 / 12.49 | Defining trained comparator | Table 5, PDF p. 19 |
| XML + TEF | TVR-trained; video+subtitle+temporal endpoint feature | 7.88 / 21.84 | 3.32 / 13.41 | Trained, bias-sensitive context | Table 5, PDF p. 19 |

TEF is not a neutral architectural improvement: the paper explicitly uses it
to expose temporal-position bias. A clean VidXP comparison should report its
ordinary run separately from any position-prior control.

TVR also defines simpler tasks. On the validation split, XML scores
`30.75 / 51.20` at tIoU .5 and `13.41 / 31.11` at tIoU .7 for SVMR
`R@1 / R@5` (Table 10, PDF p. 22). For whole-video retrieval, XML scores
`16.54 / 38.11 / 50.41 / 88.22` at `R@1/5/10/100` (Table 11, PDF p. 23).
Those validation scores must not be mixed with the `test-public` VCMR table.

### TVR-Ranking: graded ranked video-moment retrieval

Sources: [SIGIR-AP paper](https://arxiv.org/pdf/2407.06597),
[official repository](https://github.com/Ranking-VMR/TVR-Ranking), and
[released annotations](https://huggingface.co/axgroup/TVR-Ranking).

TVR-Ranking replaces the single-answer VCMR framing with graded relevance for
multiple moments. The table below uses the official test split, `NDCG@20`, and
the pseudo-training-set size selected by validation `NDCG@20` at tIoU `.5`:
`N=20` for XML and `N=40` for CONQUER and ReLoCLNet. These are target-adapted
baselines, not frozen retrieval.

| Adapted method | Pseudo-training N | NDCG@20, tIoU ≥ .3 | NDCG@20, tIoU ≥ .5 | NDCG@20, tIoU ≥ .7 | Comparison use | Evidence |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| XML | 20 | 0.2243 | 0.1650 | 0.0664 | Target-adapted context | Table 5, PDF p. 8 |
| CONQUER | 40 | 0.1968 | 0.1851 | 0.1365 | Target-adapted context | Table 5, PDF p. 8 |
| ReLoCLNet | 40 | 0.4439 | 0.4059 | 0.2877 | Strongest listed target-adapted comparator | Table 5, PDF p. 8 |

The authors' three-seed ReLoCLNet reproduction reports test
`0.4425±0.0010 / 0.4050±0.0008 / 0.2871±0.0010` at the same three tIoU
thresholds (Table 7, PDF p. 17). Use that row if reporting uncertainty; do not
combine its means with the single-run maxima above.

### HiREST: speech-backed instructional retrieval

Sources: [CVPR 2023 paper](https://openaccess.thecvf.com/content/CVPR2023/papers/Zala_Hierarchical_Video-Moment_Retrieval_and_Step-Captioning_CVPR_2023_paper.pdf),
[official repository](https://github.com/j-min/HiREST), and
[project/data page](https://hirest-cvpr2023.github.io/).

HiREST is executable with released ASR and closely matches VidXP's
Whisper/MiniLM component family, but its queries are instructional goals rather
than conversational dialogue. The whole-video and known-video moment tasks are
reported separately.

| Task/method | Target fine-tuning | Published result | Comparison use | Evidence |
| --- | --- | --- | --- | --- |
| Video retrieval, CLIP-B/32, 20 frames | No | R@1/5/10 = 13.0 / 33.3 / 41.2 | Frozen visual baseline | Table 2, PDF p. 6 |
| Video retrieval, EVA-CLIP-G/14, 20 frames | No | R@1/5/10 = 26.4 / 51.1 / 61.5 | Strong frozen visual baseline | Table 2, PDF p. 6 |
| Moment retrieval, CLIP-B/32, 8-frame span | No | R@.5/.7 = 34.02 / 15.72 | Frozen similarity/localization baseline | Table 3, PDF p. 7 |
| Moment retrieval, EVA-CLIP-G/14, 8-frame span | No | R@.5/.7 = 38.27 / 19.33 | Strong frozen similarity/localization baseline | Table 3, PDF p. 7 |
| Moment retrieval, BMT | No | R@.5/.7 = 43.56 / 10.57 | External-model context | Table 3, PDF p. 7 |
| Moment retrieval, BMT | Yes | R@.5/.7 = 71.91 / 39.18 | Target-trained context | Table 3, PDF p. 7 |
| Moment retrieval, Joint | Yes | R@.5/.7 = 73.32 / 32.60 | Target-trained context | Table 3, PDF p. 7 |

On HiREST's published known-video moment task, a released-ASR run measures
transcript embedding and within-video scoring/localization. It does not by
itself measure transcription accuracy or corpus-level indexing/video retrieval.
An additional raw-audio run would be needed to evaluate WhisperX.

### QuerYD: narrated video retrieval and oracle-proposal ranking

Sources: [ICASSP 2021 paper](https://arxiv.org/pdf/2011.11071),
[official dataset page](https://www.robots.ox.ac.uk/~vgg/data/queryd/), and
[official downloader](https://github.com/oncescuandreea/QuerYD_downloader).
The dataset page identifies the
[Collaborative Experts repository](https://github.com/albanie/collaborative-experts)
as the experiment code and precomputed-feature source.

The paragraph-level table is trained and evaluated on the paper's QuerYD split;
values are mean ± standard deviation over three seeds. E2EWS is used without
QuerYD fine-tuning, while MoEE and Collaborative Experts (CE) are trained on
QuerYD.

| Method | Target training | Text→video R@1 / R@5 / R@10 | Video→text R@1 / R@5 / R@10 | Comparison use | Evidence |
| --- | --- | --- | --- | --- | --- |
| E2EWS | No; externally weakly supervised | 13.5±0.0 / 27.5±0.0 / 34.5±0.0 | 12.4±0.0 / 23.8±0.0 / 30.8±0.0 | Off-dataset context | Table 3, PDF p. 4 |
| MoEE | Yes | 11.6±1.3 / 30.2±3.0 / 43.2±3.1 | 13.0±3.1 / 30.9±2.0 / 43.0±2.8 | Target-trained context | Table 3, PDF p. 4 |
| CE | Yes | 13.9±0.8 / 37.6±1.2 / 48.3±1.4 | 13.7±0.7 / 35.2±2.7 / 46.9±3.2 | Target-trained context | Table 3, PDF p. 4 |

The paper's “clip localisation” result is not unrestricted start/end
prediction. It supplies ground-truth temporal proposals and ranks them:

| Oracle-proposal method | Text→segment R@1 / R@5 / R@10 | Segment→text R@1 / R@5 / R@10 | Comparison use | Evidence |
| --- | --- | --- | --- | --- |
| E2EWS | 6.7±0.0 / 14.7±0.0 / 20.4±0.0 | 8.4±0.0 / 15.4±0.0 / 19.8±0.0 | External weak-supervision context | Table 6, PDF p. 4 |
| MoEE | 19.0±0.8 / 38.9±1.0 / 47.9±0.7 | 19.8±0.2 / 39.6±0.6 / 47.6±0.1 | Target-trained oracle-proposal context | Table 6, PDF p. 4 |
| CE | 18.2±0.5 / 38.1±0.8 / 46.8±0.4 | 18.1±0.6 / 37.3±0.5 / 45.9±0.6 | Target-trained oracle-proposal context | Table 6, PDF p. 4 |

VidXP can reproduce that ranking protocol using the supplied proposals. A free
boundary-prediction experiment would be a new adaptation and needs its own
baseline rather than borrowing Table 6.

### HERO / How2R: pretrained video-plus-subtitle retrieval

Sources: [EMNLP 2020 paper and data](https://aclanthology.org/2020.emnlp-main.161/)
and [official repository](https://github.com/linjieli222/HERO).

HERO is heavily pretrained and then fine-tuned downstream, so these scores are
upper context rather than frozen VidXP peers. The paper evaluates corpus moment
retrieval at tIoU greater than `.7`; Table 3 uses test splits.

| Method/task | R@1 | R@10 | R@100 | Comparison use | Evidence |
| --- | ---: | ---: | ---: | --- | --- |
| XML, TVR | 3.25 | 13.41 | 30.52 | TVR-trained baseline | Table 3a, PDF p. 8 |
| HERO, TVR | 6.21 | 19.34 | 36.66 | Pretrained and target-fine-tuned upper context | Table 3a, PDF p. 8 |
| HERO, How2R | 3.85 | 12.73 | 21.06 | Defining pretrained/fine-tuned How2R result | Table 3a, PDF p. 8 |

The `How2R` SOTA-baseline row (`2.06 / 8.96 / 13.27`) is the authors' new-task
baseline; Table 3 identifies XML as the task-specific model family. Raw How2R
queries and subtitles are released in the official
[How2R/How2QA repository](https://github.com/ych133/How2R-and-How2QA), linked
by the HERO repository rather than ACL Anthology. That repository still says
video features and process/baseline code will be released later, while HERO
says How2R features/code will be available soon. How2R is therefore not a
turnkey reproduction from the currently documented official artifacts; present
media availability and redistribution rights remain unresolved.

### CAL/STAL: the pre-TVR corpus-moment protocol

Sources: [2019 paper](https://arxiv.org/abs/1907.12763) and
[official MIT-licensed repository](https://github.com/escorciav/moments-retrieval).
This work converts DiDeMo and Charades-STA from known-video localization into
exhaustive video-corpus moment retrieval. Table 5's test result averages recall
at tIoU `.5` and `.7`; it is neither the original DiDeMo metric nor the original
Charades-STA metric.

| Dataset/method | R@1 | R@10 | R@100 | Median rank | Comparison use |
| --- | ---: | ---: | ---: | ---: | --- |
| DiDeMo, MCN+TEF | 1.03 | 5.76 | 26.67 | 354 | Target-trained corpus-conversion baseline |
| DiDeMo, STAL | 2.25 | 10.40 | 36.46 | 234 | Target-trained corpus-conversion context |
| Charades, MCN+TEF | 0.23 | 1.24 | 5.55 | 3,902 | Target-trained corpus-conversion baseline |
| Charades, STAL | 0.31 | 2.02 | 9.80 | 2,751 | Target-trained corpus-conversion context |

The repository is useful lineage and evaluator context, but its legacy
dependencies and unresolved data-preparation TODOs mean a fresh local snapshot
must be pinned before treating it as executable evidence.

### VERIFIED: fine-grained corpus video and moment retrieval

Sources: [NeurIPS paper](https://arxiv.org/html/2410.08593),
[supplement](https://proceedings.neurips.cc/paper_files/paper/2024/file/477929b8d45ab759795b7aac94329b08-Supplemental-Datasets_and_Benchmarks_Track.pdf),
and [released annotations/features](https://github.com/hlchen23/VERIFIED).
VERIFIED adds fine-grained captions to Charades, DiDeMo, and ActivityNet, making
partially matched moments/candidates the central retrieval challenge. The
paper's separately generated disturbed hard negatives train its noise evaluator;
they are not explicit benchmark-negative records.
Table 4 evaluates models trained on each corresponding FIG dataset; these are
not frozen or cross-dataset scores. ActivityNet-FIG uses `val_2`.
Each result is `VCMR R@1/R@10 at .5 | VCMR R@1/R@10 at .7 |
VR R@1/R@10`.

| Dataset | HERO | XML | ReLoCLNet | CONQUER | SQuiDNet |
| --- | --- | --- | --- | --- | --- |
| Charades-FIG | 0.11/0.40 \| 0.05/0.24 \| 1.69/11.51 | 1.05/4.33 \| 0.43/2.26 \| 2.80/14.11 | 0.78/2.88 \| 0.30/1.56 \| 2.42/12.61 | 1.21/5.46 \| 0.65/2.93 \| 2.80/14.11 | 2.61/11.59 \| 0.94/6.05 \| 11.67/44.01 |
| DiDeMo-FIG | 0.24/1.75 \| 0.17/1.08 \| 8.48/39.52 | 3.19/14.05 \| 2.32/10.69 \| 14.83/53.95 | 3.74/15.62 \| 1.92/9.84 \| 14.08/50.88 | 5.48/22.33 \| 3.66/15.87 \| 14.83/53.95 | 2.89/11.94 \| 0.52/1.99 \| 16.94/59.26 |
| ActivityNet-FIG | 1.46/4.89 \| 0.75/2.60 \| 7.95/36.49 | 2.81/12.19 \| 1.63/7.04 \| 13.46/49.99 | 3.72/15.94 \| 2.23/9.24 \| 17.49/56.49 | 2.95/13.31 \| 1.63/7.04 \| 13.46/49.99 | 4.66/17.12 \| 2.10/9.85 \| 32.57/87.93 |

Table 5 also reports CONQUER known-video SVMR as
`R@1/R@10 at .5 | R@1/R@10 at .7`: Charades-FIG
`31.99/76.05 | 15.08/50.19`, DiDeMo-FIG
`34.06/95.54 | 20.81/82.48`, and ActivityNet-FIG
`26.57/71.11 | 13.41/41.97`. CONQUER and SQuiDNet consume XML top-`K`
candidates, so they are two-stage systems. The repository releases annotations
and pre-extracted features but no standalone implementation/evaluator or
explicit license; source-dataset media terms still apply. These scores are
citable trained ceilings, while a VidXP execution remains partially blocked.

### mTVR: English/Chinese multilingual TVR

Sources: [ACL-IJCNLP paper](https://aclanthology.org/2021.acl-short.92.pdf) and
[official repository](https://github.com/jayleicn/mTVRetrieval).
On `test-public`, the principal VCMR `R@1` scores are:

| Method | English, tIoU .5 / .7 | Chinese, tIoU .5 / .7 | Comparison use |
| --- | --- | --- | --- |
| XML | 7.25 / 3.25 | 5.91 / 2.57 | Monolingual target-trained baseline |
| mXML | 8.30 / 3.82 | 6.76 / 3.20 | Joint English/Chinese target-trained context |

The validation modality/query-type ablation for mXML reports video input on
visual queries `4.12/1.89` English and `3.73/1.86` Chinese; subtitle input on
subtitle queries `6.33/2.90` and `4.15/1.97`; and video+subtitle input on joint
queries `8.29/4.09` and `5.89/3.11`, all `R@1` at tIoU `.5/.7`. These are
selected matching input/query-type conditions, not aggregate modality results.
This measures only English and Chinese—not Urdu—and supplied subtitles bypass
ASR. Hidden test ground truth, gated TV media, and roughly 24 GB of released
features constrain reproduction.

### TREC Podcasts: transcript-only segment retrieval

Source: [TREC 2020 Podcasts overview](https://trec.nist.gov/pubs/trec29/papers/OVERVIEW.P.pdf).
Table 4's fixed-segment results are:

| Run | nDCG | nDCG@30 | P@10 | Comparison use |
| --- | ---: | ---: | ---: | --- |
| BM25 baseline | 0.52 | 0.40 | 0.49 | Lexical transcript baseline |
| Query-likelihood baseline | 0.52 | 0.40 | 0.48 | Lexical transcript baseline |
| BERT description reranker | 0.43 | 0.48 | 0.57 | Neural reranking context |
| UMD IR run 3 | 0.67 | 0.52 | 0.60 | Best submitted-run context |

This is semantic search over supplied podcast transcripts and fixed segments,
with no visual modality and no ASR test. Spotify no longer distributes the
evaluation corpus, so the values are citation context rather than a runnable
VidXP target.

## Visual scene, whole-video, and temporal retrieval

### QVHighlights: official test versus `val-filt`

Official sources: [Moment-DETR paper](https://proceedings.neurips.cc/paper_files/paper/2021/file/62e0973455fd26eb03e91d5741a4a3bb-Paper.pdf)
and [data/code/evaluator](https://github.com/jayleicn/moment_detr).
The original test used private CodaLab-era labels. Scores below are
`R1@.5 / R1@.7 | mAP@.5 / mAP@.75 / average mAP | highlight mAP / HIT@1`.

| Method | Training/input | Official-test result | Comparison use | Evidence/artifact |
| --- | --- | --- | --- | --- |
| CLIP + Watershed | Frozen CLIP; center frame of each two-second clip; watershed proposals | 16.88 / 5.19 \| 18.11 / 7.00 / 7.67 \| 31.30 / 61.04 | Closest direct frozen official-test baseline | Moment-DETR Table 3, PDF p. 8; official evaluator above |
| Moment-DETR | QVHighlights-trained | 52.89±2.3 / 33.02±1.7 \| 54.82±1.7 / 29.40±1.7 / 30.73±1.4 \| 35.69±0.5 / 55.60±1.6 | Supervised context | Same table |
| Moment-DETR with pretraining | ASR-caption pretraining plus QVHighlights fine-tuning | 59.78±0.3 / 40.33±0.5 \| 60.51±0.2 / 35.36±0.4 / 36.14±0.25 \| 37.43±0.2 / 60.17±0.7 | Pretrained/supervised upper context | Same table |

[QD-DETR](https://openaccess.thecvf.com/content/CVPR2023/html/Moon_Query-Dependent_Video_Representation_for_Moment_Retrieval_and_Highlight_Detection_CVPR_2023_paper.html)
reports `63.06±1.0 / 45.10±0.7 | 63.04±0.9 / 40.10±1.0 /
40.19±0.6 | 39.04±0.3 / 62.87±0.6` for its target-trained video+audio
model (Table 1, PDF p. 6); the
[official repository](https://github.com/wjun0830/QD-DETR#qvhighlights-pretrained-checkpoints)
provides the matching video+audio checkpoint. It is a supervised ceiling, not a
frozen peer.

The off-the-shelf study uses a different, exactly released 1,434-video validation
subset, [`highlight_val_filt_release.jsonl`](https://gist.github.com/ajd12342/2cf640eff982c32dee509e6101dbede5).
Its scores omit highlight metrics and are
`R1@.5 / R1@.7 | mAP@.5 / mAP@.75 / average mAP`.

| Method | `val-filt` result | Comparison use | Evidence |
| --- | --- | --- | --- |
| SlidingWindow + CLIP | 29.71 / 8.86 \| 35.26 / 8.31 / 13.42 | Direct only on this exact subset | [Off-the-Shelf VMR paper](https://proceedings.mlr.press/v203/diwan23a.html), Table 2, PDF p. 7 |
| ShotDetect + CLIP | 40.24 / 25.94 \| 41.74 / 24.11 / 24.82 | Closest proposal+frozen-CLIP comparison; threshold selected on validation | Same table |
| ShotDetect + CLIP + SimpleWatershed | 48.33 / 30.96 \| 46.94 / 25.75 / 27.96 | Strongest relevant zero-shot row on this subset | Same table |
| Moment-DETR with pretraining, authors' rerun | 59.74 / 41.10 \| 59.90 / 35.42 / 36.19 | Supervised context | Tables 1–2, PDF pp. 5 and 7 |

No official end-to-end implementation or prediction file for the Off-the-Shelf
pipeline was found. The subset, paper, PySceneDetect, and CLIP dependencies are
available, so a reproduction must record its own code and threshold.

### DiDeMo: do not mix temporal and whole-video adaptations

The original [DiDeMo paper](https://openaccess.thecvf.com/content_ICCV_2017/papers/Hendricks_Localizing_Moments_in_ICCV_2017_paper.pdf)
uses known-video localization over 21 fixed moments. The
[official repository](https://github.com/LisaAnne/LocalizingMoments) provides
annotations, evaluator, code, and a released-model expected output.

| Original test method | Rank@1 | Rank@5 | mIoU | Training/use | Evidence |
| --- | ---: | ---: | ---: | --- | --- |
| Chance | 3.75 | 22.50 | 22.64 | Sanity floor | Table 3, PDF p. 7 |
| Moment-frequency prior | 19.40 | 66.38 | 26.65 | Nonsemantic floor | Table 3, PDF p. 7 |
| MCN | 28.10 | 78.21 | 41.08 | DiDeMo-supervised context | Table 3, PDF p. 7 |
| MCN released-model rerun | 27.08 | 78.53 | 40.53 | Official reproducibility reference | Repository expected test output |

Later papers concatenate a video's descriptions and perform whole-video
paragraph retrieval instead. Their text→video values are not temporal scores:

| Whole-video method | Target training | R@1 / R@5 / R@10 | MedR | Evidence/artifact |
| --- | --- | --- | ---: | --- |
| Frozen in Time | Zero-shot, CC3M+WebVid-2M pretrained | 21.1 / 46.0 / 56.2 | 7 | [Paper Table 6, p. 8](https://openaccess.thecvf.com/content/ICCV2021/html/Bain_Frozen_in_Time_A_Joint_Video_and_Image_Encoder_for_ICCV_2021_paper.html); [pretrained weight](https://github.com/m-bain/frozen-in-time#-pretrained-weights) |
| Frozen in Time | DiDeMo fine-tuned | 31.0 / 59.8 / 72.4 | 3 | Same paper/table; no target checkpoint identified |
| CLIP4Clip MeanP | DiDeMo fine-tuned | 43.4 / 70.2 / 80.6 | 2 | [Paper Table 5, p. 7](https://arxiv.org/abs/2104.08860); [official code](https://github.com/ArrowLuo/CLIP4Clip) |
| X-CLIP | DiDeMo fine-tuned | 47.8 / 79.3 / not reported | Not reported | [Paper Table 4, p. 6](https://arxiv.org/abs/2207.07285); [official code](https://github.com/xuguohai/X-CLIP) |

### Charades-STA: original and filtered splits

The original [TALL/CTRL paper](https://arxiv.org/abs/1705.02101) uses
13,898 train and 4,233 test query-moment pairs. Results are
`R1@.5 / R1@.7 | R5@.5 / R5@.7`.

| Method | Original-split result | Use | Evidence/artifact |
| --- | --- | --- | --- |
| Random | 8.51 / 3.03 \| 37.12 / 14.06 | Sanity floor | Table 2, PDF p. 7 |
| CTRL `reg-np` | 23.63 / 8.89 \| 58.92 / 29.52 | Supervised context | Same table; [official code/data](https://github.com/jiyanggao/TALL) |

Later work commonly uses a filtered 12,408/3,720 split:

| Method | Training | Filtered-split result | Evidence/artifact |
| --- | --- | --- | --- |
| Moment-DETR | Charades-supervised | R1@.5 53.63; R1@.7 31.37 | Moment-DETR Table 6, PDF p. 10; no exact Charades checkpoint identified |
| QD-DETR, SlowFast+CLIP | Charades-supervised | R1@.5 57.31; R1@.7 32.55 | QD-DETR Table 3, PDF p. 6; no exact Charades checkpoint identified |
| UniVTG ZS | 4.2M temporal-label pretraining; no Charades fine-tuning | R1@.3 44.09; @.5 25.22; @.7 10.03; mIoU 27.12 | [UniVTG Table 3, p. 7](https://openaccess.thecvf.com/content/ICCV2023/html/Lin_UniVTG_Towards_Unified_Video-Language_Temporal_Grounding_ICCV_2023_paper.html); [pretraining artifacts](https://github.com/showlab/UniVTG/blob/main/install.md) |
| UniVTG with pretraining | Pretrained and Charades-fine-tuned | R1@.3 72.63; @.5 60.19; @.7 38.55; mIoU 52.17 | Same table; [checkpoint/config/predictions/logs](https://github.com/showlab/UniVTG/blob/main/model.md#moment-retrieval) |

No result may be called “Charades-STA” without naming which split was used.

### MSR-VTT 9K/1K-A: frozen and trained whole-video retrieval

The score vector is text→video `R@1 / R@5 / R@10 | MedR / MnR`.

| Method | Target training/pretraining | Published result | Comparison use | Evidence/artifact |
| --- | --- | --- | --- | --- |
| CLIP-straight | No MSR-VTT fine-tuning | 31.2 / 53.7 / 64.2 \| 4 / not reported | Direct frozen baseline | [CLIP4Clip Table 1(b), p. 6](https://arxiv.org/abs/2104.08860) |
| CLIP4Clip MeanP ZS | No MSR-VTT fine-tuning | 30.6 / 54.4 / 64.3 \| 4 / 41.8 | Direct frozen baseline | Same paper, Table 7, p. 9 |
| Frozen in Time ZS | CC3M+WebVid-2M; no MSR-VTT fine-tuning | 23.2 / 44.6 / 56.6 \| 7 / not reported | Direct pretrained/frozen baseline | [Frozen Table 4, p. 8](https://openaccess.thecvf.com/content/ICCV2021/html/Bain_Frozen_in_Time_A_Joint_Video_and_Image_Encoder_for_ICCV_2021_paper.html); [weight](https://github.com/m-bain/frozen-in-time#-pretrained-weights) |
| CLIP4Clip MeanP | MSR-VTT fine-tuned | 43.1 / 70.4 / 80.8 \| 2 / 16.2 | Supervised context | CLIP4Clip Table 1(c), p. 6 |
| CLIP4Clip seqTransf | MSR-VTT fine-tuned | 44.5 / 71.4 / 81.6 \| 2 / 15.3 | Supervised context | Same table |
| X-CLIP | MSR-VTT fine-tuned | 49.3 / 75.8 / 84.8 \| 2 / 12.2 | Supervised context | [X-CLIP Table 1, p. 6](https://arxiv.org/abs/2207.07285) |

The [canonical split/caption archive](https://github.com/ArrowLuo/CLIP4Clip/releases/download/v0.0/msrvtt_data.zip)
and [raw-video mirror](https://www.robots.ox.ac.uk/~maxbain/frozen-in-time/data/MSRVTT.zip)
were HTTP-checked by the result audit. CLIP4Clip and X-CLIP provide code and
scripts but not the target-trained checkpoints/predictions behind every row.

### LoVR: bidirectional long-video and predefined-clip retrieval

Sources: [Web Conference 2026 paper](https://arxiv.org/html/2505.13928),
[accepted-paper listing](https://www2026.thewebconf.org/accepted/research-tracks.html),
[project](https://lovrbench.github.io/),
[MIT-licensed code](https://github.com/TechNomad-ds/LoVR-benchmark), and
[CC BY-NC-SA data](https://huggingface.co/datasets/debugger123/LoVR-benchmark).
The models below are used zero-shot/pretrained. Results are `R@1/R@5/R@10`.

| Method | Text→video | Video→text | Text→clip | Clip→text |
| --- | --- | --- | --- | --- |
| CLIP | 23.34/43.90/54.39 | 16.70/35.55/47.11 | 18.78/37.28/46.31 | 18.29/35.99/44.76 |
| VideoCLIP-XL-v2 | 29.98/55.67/67.67 | 25.05/52.03/64.03 | 55.34/78.29/84.95 | 48.54/73.82/81.75 |
| LanguageBind-Video | 42.61/66.60/77.94 | 37.47/60.39/73.23 | 40.30/65.13/74.26 | 35.83/61.12/70.84 |

LoVR contains 467 videos and, according to the paper, 40,804 predefined scene
clips used as a test-only benchmark. Clip retrieval therefore ranks supplied
clips; it does not predict temporal boundaries. The current Hugging Face
release exposes differently named/counting splits (including a `train` split
with a different row count), conflicting with the paper. A run is valid only
after pinning the dataset revision and documenting how that discrepancy was
resolved.

### EclipSE: long-range audiovisual paragraph-to-video retrieval

Source: [ECCV 2022 paper](https://www.ecva.net/papers/eccv_2022/papers_ECCV/papers/136940405.pdf).
The paper repurposes QVHighlights, DiDeMo, and YouCook2 by concatenating segment
captions into paragraph-to-whole-video queries; Charades instead uses its
provided single descriptions for text-to-video retrieval. None is evaluated as
its original temporal task here. Target-trained text-to-video `R@1` is:

| Method | QVHighlights | DiDeMo | YouCook2 | Charades |
| --- | ---: | ---: | ---: | ---: |
| CLIP4Clip | 70.2 | 42.5 | 37.6 | 13.9 |
| EclipSE | 70.8 | 44.2 | 38.5 | 15.7 |

On ActivityNet `val1`, EclipSE with a ViT-B/32 backbone and 32 frames reports
`R@1/5/10 42.3/73.2/83.8, MnR 8.2`; with a ViT-B/16 backbone and the same
32 frames, `45.3/75.7/86.2, MnR 6.2`. All are target-trained whole-video
ceilings.

### TACoS, MSVD, and cross-dataset failure context

UniVTG's [ICCV 2023 paper](https://openaccess.thecvf.com/content/ICCV2023/html/Lin_UniVTG_Towards_Unified_Video-Language_Temporal_Grounding_ICCV_2023_paper.html)
reports TACoS `R@1` at IoU `.3/.5/.7 | mIoU`: 2D-TAN
`40.01/27.99/12.92 | 27.22`; target-trained UniVTG
`51.44/34.97/17.35 | 33.60`; pretrained plus target-trained
`56.11/43.44/24.27 | 38.63`; and explicitly zero-shot UniVTG
`5.17/1.27/0.27 | 4.40`. That last row is zero-shot only with respect to
TACoS fine-tuning; it still uses 4.2M temporal-label pretraining examples. This
narrow cooking-domain known-video task is a useful stress test but not a
corpus-ranking result.

Q2E's [AACL paper](https://aclanthology.org/2025.ijcnlp-long.121.pdf) reports
zero-shot MSVD `R@1/5/10 63.77/85.30/89.99`; its cited target-trained
Cap4Video row is `51.80/80.80/88.30`. Both use the later standard
1,200/100/670 train/validation/test retrieval adaptation, not a protocol from
the defining MSVD paper. MSVD is whole-video retrieval and contains no audio
track in the standard prepared release, so it tests visual retrieval only.

The [MoLEF benchmark study](https://proceedings.mlr.press/v238/chae24a.html)
shows why in-distribution trained scores are weak evidence for VidXP
generalization. CMIN's `R@1@.5` falls from IID to OOD:
ActivityNet `50.39→8.94`, Charades `50.95→32.42`, and YouCook2
`34.38→3.60`. These are methodology warnings, not VidXP baselines.

### MAD-v1: zero-shot full-movie localization

Sources: [CVPR 2022 paper](https://openaccess.thecvf.com/content/CVPR2022/html/Soldan_MAD_A_Scalable_Dataset_for_Language_Grounding_in_Videos_From_CVPR_2022_paper.html)
and [official baseline repository](https://github.com/Soldelli/MAD).
Each cell is `R@1 / R@5 / R@10 / R@50 / R@100`.

| Method | IoU .1 | IoU .3 | IoU .5 | Comparison use | Evidence |
| --- | --- | --- | --- | --- | --- |
| CLIP, zero-shot full proposal ranking | 6.57 / 15.05 / 20.26 / 37.92 / 47.73 | 3.13 / 9.85 / 14.13 / 28.71 / 36.98 | 1.39 / 5.44 / 8.38 / 18.80 / 24.99 | Strongest direct long-film frozen reference found | Table 2, PDF p. 6 |
| VLG-Net, MAD-trained | 3.64 / 11.66 / 17.89 / 39.78 / 51.24 | 2.76 / 9.31 / 14.65 / 34.27 / 44.87 | 1.65 / 5.99 / 9.77 / 24.93 / 33.95 | Supervised context | Same table |

MAD annotations/features require an access form and NDA; raw movies are not
distributed. A VidXP run requires separately acquired lawful movies and the exact
proposal construction.

### Ego4D NLQ and ActivityNet caveats

UniVTG's Ego4D NLQ v1 validation results are:

| Method | R1@.3 | R1@.5 | R1@.7 | mIoU | Comparison use |
| --- | ---: | ---: | ---: | ---: | --- |
| UniVTG ZS | 6.48 | 3.48 | 1.16 | 4.63 | Conditional: its 4.2M pretraining includes 1.8M Ego4D-derived point labels |
| UniVTG | 7.28 | 3.95 | 1.32 | 4.91 | NLQ-supervised context |
| UniVTG with pretraining | 11.74 | 7.54 | 3.25 | 7.88 | Pretrained and NLQ-supervised context |

Source: [UniVTG Table 3, PDF p. 7](https://openaccess.thecvf.com/content/ICCV2023/html/Lin_UniVTG_Towards_Unified_Video-Language_Temporal_Grounding_ICCV_2023_paper.html);
the repository provides a
[checkpoint/config/prediction bundle](https://github.com/showlab/UniVTG/blob/main/model.md#moment-retrieval).
Do not mix v1 validation with NLQ v2 or public-test leaderboards.

For ActivityNet Captions, CLIP4Clip (`R@1 40.5, R@5 72.4, R@50
98.1, MedR 2, MnR 7.4`) and X-CLIP (`R@1 46.2, R@5 75.5, MnR
6.8`) use a paragraph-to-whole-video `val1` adaptation, not timestamp
localization. Sources are CLIP4Clip Table 4 (PDF p. 7) and X-CLIP Table 5
(PDF p. 7). A temporal VidXP comparison needs one explicitly selected later
evaluator, such as the public [MoLEF framework](https://github.com/snuviplab/MoLEF).

## Actor and person clustering

### BCL: BBT/Buffy unknown-number clustering

Sources: [ICCV 2019 paper](https://openaccess.thecvf.com/content_ICCV_2019/papers/Tapaswi_Video_Face_Clustering_With_Unknown_Number_of_Clusters_ICCV_2019_paper.pdf),
[official repository](https://github.com/makarandtapaswi/BallClustering_ICCV2019),
[evaluator](https://github.com/makarandtapaswi/BallClustering_ICCV2019/blob/master/evaluate.py),
[metrics](https://github.com/makarandtapaswi/BallClustering_ICCV2019/blob/master/metrics.py),
and [checkpoints](https://github.com/makarandtapaswi/BallClustering_ICCV2019/tree/master/model_chkpts).
The repository also links labels/tracks and about 519 MB of supplied
SE-ResNet50-256 features. Full training code is still marked “coming soon.”

Table 5 evaluates the authors' MovieGraphs-supervised BCL representation and
learned unknown-`K` HAC threshold. “+FT” uses automatically generated
same-episode pairs for episode fine-tuning.

| Method | Dataset aggregation | Ground-truth / predicted clusters | NMI | WCP | Evidence |
| --- | --- | ---: | ---: | ---: | --- |
| BCL | BBT season 1, six episodes | 103 / 47 | 73.22 | 89.36 | Table 5, proceedings p. 5032 |
| BCL | Buffy season 5, six episodes | 109 / 71 | 71.23 | 83.62 | Same table |
| BCL | All 12 episodes | 212 / 116 | 75.32 | 82.81 | Same table |
| BCL + episode FT | BBT season 1, six episodes | 103 / 69 | 88.26 | 94.11 | Same table |
| BCL + episode FT | Buffy season 5, six episodes | 109 / 78 | 77.05 | 86.64 | Same table |
| BCL + episode FT | All 12 episodes | 212 / 126 | 80.42 | 85.84 | Same table |

A run on BCL's supplied embeddings only validates the evaluator/BCL path. A
VidXP embedding comparison requires the identical tracks/labels, VidXP
embeddings for those tracks, and the same metric implementation. An end-to-end
VidXP run additionally requires lawful episode media.

### C1C: constrained BBT/Buffy/Friends clustering

Sources: [BMVC 2020 paper](https://www.bmva-archive.org.uk/bmvc/2020/assets/papers/0899.pdf),
[official Friends dataset page](https://www.robots.ox.ac.uk/~vgg/research/c1c/),
and [nominal repository](https://github.com/vkalogeiton/c1c). The repository
contains only a README and still says the implementation is coming soon.
C1C itself is training-free but uses must-link/cannot-link video constraints;
scores are frame-level.

| Method | Cluster-count condition | BBT six-episode average NMI / WCP | Buffy six-episode average NMI / WCP | Evidence |
| --- | --- | --- | --- | --- |
| FINCH | BCL-estimated episode `K` | 78.7 / 92.4 | 72.9 / 83.3 | Table 4, paper p. 9 |
| BCL | BCL-estimated episode `K` | 85.7 / 90.8 | 78.8 / 85.0 | Same table |
| C1C | BCL-estimated episode `K` | 87.8 / 94.8 | 81.4 / 87.1 | Same table |
| FINCH | Oracle episode `K` | 80.5 / 90.8 | 75.3 / 82.9 | Same table |
| BCL | Oracle episode `K` | 84.9 / 93.9 | 77.6 / 86.5 | Same table |
| C1C | Oracle episode `K` | 84.5 / 95.3 | 79.1 / 88.1 | Same table |

On Friends, Table 1 (paper p. 8) reports WCP `69.7` for FINCH and
`77.0` for C1C under each method's selected partition. The paper discusses NMI
for Friends graphically but does not tabulate one aggregate value, so none is
reconstructed here. The dataset page distributes info files, shots, tracks,
features, and parsing code, but not the C1C algorithm.

### VPCD: face/body/voice person clustering

Sources: [ICCVW 2021 paper](https://openaccess.thecvf.com/content/ICCV2021W/CVEU/papers/Brown_Face_Body_Voice_Video_Person-Clustering_With_Multiple_Modalities_ICCVW_2021_paper.pdf),
[official data page](https://www.robots.ox.ac.uk/~vgg/data/Video_Person_Clustering/),
and [repository/download pointer](https://github.com/Andrew-Brown1/Video_Person_Clustering).
Table 2 averages automatic-termination results across TBBT, Buffy, Sherlock,
Friends, *Hidden Figures*, and *About Last Night*.

| Method | Modalities | WCP | NMI | Cluster precision | Cluster recall | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B-ReID | Body | 58.5 | 42.9 | 48.5 | 42.4 | Table 2, proceedings p. 3190 |
| B-C1C | Face+body | 82.5 | 67.0 | 49.3 | 55.6 | Same table |
| MuHPC-minus | Face | 86.1 | 72.9 | 71.7 | 69.8 | Same table |
| MuHPC-v | Face+voice | 86.4 | 73.5 | 72.3 | 69.7 | Same table |
| MuHPC-b | Face+body | 88.2 | 77.1 | 74.0 | 70.0 | Same table |
| MuHPC | Face+body+voice | 88.6 | 78.8 | 74.8 | 71.5 | Same table |

These values use supplied face/body/voice tracks and modality features; voice
segments were manually diarized. For a face-only VidXP comparison, Table 3's
automatic-termination `MuHPC-minus` rows are the relevant context:

| Dataset | Method | WCP | NMI | Cluster precision | Cluster recall | Sum predicted clusters |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| TBBT | BCL | 90.8 | 85.7 | Not reported | Not reported | 83 |
| TBBT | C1C | 89.2 | 87.4 | 29.1 | 40.9 | 41 |
| TBBT | MuHPC-minus | 99.4 | 97.8 | 87.8 | 88.6 | 168 |
| Buffy | BCL | 85.0 | 78.8 | Not reported | Not reported | 121 |
| Buffy | C1C | 66.3 | 68.8 | 14.9 | 27.1 | 40 |
| Buffy | MuHPC-minus | 96.1 | 92.8 | 85.6 | 85.5 | 223 |
| Friends | C1C | 88.2 | 89.8 | 62.4 | 73.2 | 185 |
| Friends | MuHPC-minus | 98.7 | 94.9 | 98.1 | 94.0 | 543 |
| Sherlock | C1C | 76.3 | 50.3 | 20.2 | 41.0 | 25 |
| Sherlock | MuHPC-minus | 86.7 | 60.3 | 79.1 | 71.2 | 96 |

The repository warns that its current downloadable dataset does not have the
exact paper statistics and promises a corrected release. These are valid
published citations, but the current package is not a clean reproduction target.

### VideoClusterNet and MovieFaceCluster

Source: [ECCV 2024 paper](https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/04432.pdf).
On the six-episode protocol, Table 1 reports track-level WCP/clustering
accuracy, weighted by cluster track count:

| Method | BBT | Buffy | Training/use |
| --- | ---: | ---: | --- |
| MLR | 83.71 | 66.37 | Supervised historical context |
| BCL | 89.63 | 83.62 | Supervised unknown-`K` context |
| VC-TRSF | 94.20 | Not reported | Same-video self-supervised context |
| VideoClusterNet | 98.70 | 96.10 | Same-video self-supervised, target-video adapted |

Table 3's nine MovieFaceCluster rows are `WCP / predicted-to-true cluster ratio`:
*An Elephant's Journey* `97.2/1.11`, *Armed Response* `94.1/0.93`,
*Angel Of The Skies* `85.9/0.72`, *Death Do Us Part (2019)* `98.0/1.14`,
*American Fright Fest* `97.6/0.92`, *The Fortress* `89.3/1.02`,
*Under the Shadow* `82.5/1.88`, *The Hidden Soldier* `98.5/1.04`, and
*S.M.A.R.T. Chase* `93.8/1.50`.
The method fine-tunes ArcFace on each target video using self-supervision. No
working dataset release, code, evaluator, features, or predictions were found,
so these are published target-video-adapted ceilings only.

### EasyCom / VC-TRSF: known- and unknown-`K` egocentric clustering

Sources: [TPAMI paper](https://arxiv.org/pdf/2203.13166) and
[empty release pointer](https://github.com/ibug-group/Easycom-Clustering).
Table 7 reports `known-K NMI/WCP | unknown-K #C DIF/NMI`, where `#C DIF` is
the sum of absolute differences between predicted and true cluster counts
across all dataset videos:

| Method | BBT average | EasyCom average |
| --- | --- | --- |
| Temp Avg | 88.57/95.84 \| 62/90.26 | 65.05/78.44 \| 25/48.13 |
| SSiam | 92.34/97.01 \| 74/85.46 | 83.42/93.07 \| 27/83.89 |
| CCL | 93.98/97.11 \| 75/91.14 | 85.84/94.62 \| 82/84.61 |
| TSiam | 94.11/97.04 \| 61/92.75 | 87.10/95.84 \| 76/85.70 |
| CT-TRSF | 91.86/96.77 \| 76/90.04 | 84.48/94.49 \| 15/79.78 |
| VC-TRSF | 94.20/97.50 \| 56/92.95 | 91.01/97.45 \| 15/87.44 |

All learned methods except the nonlearned Temp Avg baseline adapt within the
target video. Clustering thresholds were
selected on BBT episode 2 or EasyCom session V09, so this is not a clean
held-out zero-shot protocol. The official repository still has no data, code,
evaluator, checkpoint, features, or predictions.

### IJB-B: generic face-template clustering

Sources: [IJB-B benchmark paper](https://openaccess.thecvf.com/content_cvpr_2017_workshops/w6/html/Whitelam_IARPA_Janus_Benchmark-B_CVPR_2017_paper.html),
[official NIST README](https://www.nist.gov/system/files/documents/2021/06/07/ijbb_challenge_documentation_readme.pdf),
and [NIST Face Challenges page](https://www.nist.gov/programs-projects/face-challenges).
The GOTS clustering results below are `B-cubed precision/recall/F-score`.

| Subprotocol ground-truth subjects | Result |
| ---: | --- |
| 32 | 0.589 / 0.298 / 0.395 |
| 64 | 0.578 / 0.302 / 0.396 |
| 128 | 0.605 / 0.352 / 0.445 |
| 256 | 0.581 / 0.362 / 0.446 |
| 512 | 0.516 / 0.328 / 0.401 |
| 1,024 | 0.485 / 0.345 / 0.403 |
| 1,845 | Memory failure; no score |

Each subprotocol also supplies a coarse subject-count hint; the first column is
not a requested output cluster count. This clusters individual images/video
frames rather than within-video tracks. The paper excludes failure-to-enroll
samples, while later evaluator defaults may count them as zero unless `-no_fte`
is used. NIST discontinued distribution in March 2023, so a new run requires a
lawful existing copy and an explicitly pinned FTE policy.

### SSiam/TSiam and ACCIO: fixed-`K` clustering versus retrieval

Source: [IEEE FG 2019 paper](https://www.cs.toronto.edu/~makarand/papers/FG2019_FClst.pdf).
For known-`K`, frame-level clustering accuracy/WCP, Table IX reports BBT-0101
TSiam `98.58`, SSiam `99.04`; Buffy-0502 TSiam `92.46`, SSiam `90.87`.
On ACCIO's 36-character condition, `precision/recall/F-score` is JFAC
`0.690/0.350/0.460`, TSiam `0.749/0.382/0.506`, and SSiam
`0.766/0.386/0.514`. At 40 clusters it is respectively
`0.711/0.352/0.471`, `0.763/0.362/0.491`, and `0.777/0.371/0.502`.
The [official SSIAM repository](https://github.com/vivoutlaw/SSIAM) contains
MATLAB code, metrics, and a claimed BBT-0101 reproduction path. Reproduction is
still blocked by unavailable media/features and legacy dependencies, not by an
absent implementation.

ACCIO's separate [ICMR 2015 retrieval paper](https://www.cs.toronto.edu/~makarand/papers/ICMR2015.pdf)
measures query-by-example face-track retrieval, not clustering. Table 4 uses
VF² descriptors with Euclidean-distance ranking under the restricted protocol:
no external data/training and no use of other ACCIO tracks. Its within-film
`P@1/P@5/P@10/mAP` results are HP1 `90.8/81.5/75.9/42.40`, HP2
`89.3/79.0/71.6/31.73`, HP3 `90.9/79.1/70.2/31.19`, HP4
`85.1/72.3/64.5/28.36`, HP5 `89.4/79.3/72.0/32.09`, HP6
`91.1/80.4/72.2/30.38`, HP7 `91.3/82.1/75.3/38.55`, and HP8
`89.4/78.6/69.7/33.21`. These two ACCIO protocols must never share one result
column.

Table 5's across-movie age-retrieval protocol is directional. For the HP-1
query row, `mAP` against HP-2 through HP-8 is
`33.1/26.8/24.5/21.5/22.2/27.4/20.0`; the within-movie HP-1 diagonal is
`42.4`. In the reverse endpoint, HP-8→HP-1 is `19.9`, showing that the matrix
is not symmetric. The movie media and a complete ready-to-run artifact package
remain unavailable.

### Erdos-Renyi end-to-end cast grouping

Source: [ICCV 2017 paper](https://openaccess.thecvf.com/content_iccv_2017/html/Jin_End-To-End_Face_Detection_ICCV_2017_paper.html).
The proposed unified pairwise balanced F-measure, `F_alpha` with `alpha=0.5`,
jointly penalizes detection misses and identity-grouping errors; it is not the
standard precision-weighted `F_beta`. With the automatically selected threshold
from LFW validation it scores BBT `0.7728`, Buffy `0.5661`, and Hannah `0.6436`;
target-data oracle-threshold values are `0.7828`, `0.6299`, and `0.6813`. This
is an end-to-end
detection+tracking+clustering protocol, not WCP/NMI, and the gated media plus
missing portable evaluator prevent a clean reproduction.

### Prior-Less: unknown-`K` end-to-end multi-face tracking

Source: [CVPR 2018 paper](https://openaccess.thecvf.com/content_cvpr_2018/papers/Lin_A_Prior-Less_Method_CVPR_2018_paper.pdf).
Table 1 reports music-video `WCP | predicted/ground-truth clusters`:
`0.89|6/6`, `0.79|6/6`, `0.85|11/11`, `0.70|4/4`, `0.73|7/8`,
`0.92|6/6`, `0.86|4/4`, and `0.92|5/5`. Table 3 reports the four
body-worn-camera videos as `0.73|4/5`, `0.80|3/3`, `0.80|2/2`, and
`0.81|3/3`. This is directly relevant unknown-`K` raw-video
detection/tracking/clustering evidence. Dataset availability and a portable
artifact package were not confirmed, so it is a non-executable published
ceiling—not a reason to omit the scores.

### Earlier known-`K` clustering and joint-tracking references

The [CVPR 2013 constrained-clustering paper](https://openaccess.thecvf.com/content_cvpr_2013/html/Wu_Constrained_Clustering_and_2013_CVPR_paper.html)
reports HMRF all-link clustering accuracy `50.30±2.73` on Buffy and
`84.39±1.47` on Notting Hill. The
[ICCV 2013 joint clustering/tracklet-linking paper](https://openaccess.thecvf.com/content_iccv_2013/html/Wu_Simultaneous_Clustering_and_2013_ICCV_paper.html)
reports `track/face accuracy`: Frontal `90.70/94.95`, Turning
`90.00/92.57`, and BBT01 `66.48/66.77`. These are known-`K` or joint
tracking protocols with unverified legacy artifacts; they are historical
context, not unknown-`K` VidXP baselines.

### Actor candidates that remain citation context

| Candidate | Published result source | Why it is not an executable comparison yet |
| --- | --- | --- |
| MovieGraphs face clustering | [MovieGraphs Section 5, PDF p. 7](https://openaccess.thecvf.com/content_cvpr_2018/papers/Vicol_MovieGraphs_Towards_Understanding_CVPR_2018_paper.pdf) reports `75.8%` WCP | One supplied-track dataset baseline; raw movies are not a turnkey distributed corpus |
| Dynamic character graph | [2020 paper](https://arxiv.org/abs/2007.14913) reports BF2006 accuracy `82.12`, NH2016 accuracy `93.84`, and V-measure `0.68`/`0.89` | Six-movie downstream act/major-character labels were expert/manual; no official code/evaluator/data package was found |
| MovieGraphs person identification | [MovieGraphs Section 5, PDF p. 7](https://openaccess.thecvf.com/content_cvpr_2018/papers/Vicol_MovieGraphs_Towards_Understanding_CVPR_2018_paper.pdf) reports `43.7%` person ID versus `13.2%` chance | Downstream identification over supplied tracks; not an unknown-`K` clustering result |

## Complete candidate-to-result disposition

This closes the earlier category error that omitted VERIFIED: a candidate may be
non-executable and still have published competitor numbers worth citing. Every
candidate in the catalog now has one of the dispositions below. “Covered” means
the exact score and protocol are transcribed above; it does not make the score
directly comparable to a future VidXP run.

| Catalog candidates | Published-result disposition | Explicit constraint |
| --- | --- | --- |
| TVR `sub-only` queries; TVR `video-only` queries; TVR `video+sub` queries | Covered under TVR/XML | Main public table combines modalities; a VidXP modality claim needs the corresponding query slice |
| HiREST, QuerYD, TVR-Ranking, How2R | Covered | Released ASR bypasses transcription; QuerYD localizes oracle proposals; How2R artifacts remain incomplete |
| TREC Podcasts | Covered as blocked citation context | Closed corpus; transcript-only fixed-segment retrieval |
| NIST OpenKWS | No portable competitor score copied | Evaluation plan defines occurrence/ATWV/timing protocol; Babel/LDC data and system-specific thresholds prevent a fair fixed baseline |
| SAVA, MediaEval 2015 | No result table suitable for reuse | Overview describes a historical BBC task; data/judgments are not presently portable |
| mTVR | Covered | English/Chinese only, supplied subtitles, hidden test labels; no Urdu inference |
| DiDeMo, QVHighlights, Charades-STA | Covered | Original task/split/evaluator must be named; later whole-video adaptations are separate |
| MSR-VTT, ActivityNet Captions, MAD, Ego4D NLQ | Covered | Split/task/access caveats stated in each section |
| VERIFIED, LoVR, TACoS, MSVD | Covered | VERIFIED is target-trained/partially blocked; LoVR has a release conflict; TACoS is known-video; MSVD is whole-video |
| LSMDC, VATEX | Covered through the SAVE/EclipSE comparator family | Later retrieval adaptations, not native temporal protocols; target-trained ceilings |
| BCL on BBT/Buffy; C1C / Friends; VPCD | Covered | Supplied tracks/features do not measure VidXP detection or embeddings unless replaced |
| Hannah; Erdos-Renyi BBT/Buffy/Hannah protocol | Covered | End-to-end unified F-measure, gated movie, not WCP/NMI |
| MovieNet | No single result can represent it | Component dataset, not one official end-to-end VidXP benchmark |
| MovieGraphs | Covered for face clustering/person ID; retrieval remains source context | Raw films gated; it is also BCL training/validation data |
| BF0502 and Notting-Hill; Frontal, Turning, and BBT01 | Covered as historical context | Known-`K` or joint tracking protocols with unverified legacy artifacts |
| Music-video and body-worn-camera sets | Covered under Prior-Less as blocked citation context | Direct unknown-`K` end-to-end actor evidence; dataset/artifact availability unconfirmed |
| ACCIO; ACCIO retrieval protocol | Covered separately | Fixed-`K` clustering and query-by-example retrieval are different tasks |
| Dynamic CIG six-movie protocol | Covered as citation context | Manual/expert downstream labels; no portable packaged evaluator |
| IJB-B | Covered | Distribution discontinued; generic image/frame templates, not video tracks; FTE policy matters |
| MovieFaceCluster / VideoClusterNet; EasyCom-Clustering | Covered as blocked trained/adapted ceilings | No executable artifact/data package validated |
| LFW / dlib protocol | No video benchmark result | Face-embedding verification provenance only |
| LongVALE; FLARE; MultiVENT 2.0; TRECVID AVS / V3C; MUVR; VALOR-32K | Covered | Each section states modality, training, scale, and access limits |
| MM-MSRVTT and TVR-1200; ContextIQ Val-1 | Covered as artifact-limited citations | Generated/custom queries and missing code/evaluator prevent reproduction |
| VectorDBBench | Configuration-dependent engineering section below | Same-machine harness only; no universal external score |
| WhisperX evaluation; MGB Challenge | No end-to-end retrieval score copied | Component transcription/alignment evaluations, not retrieval benchmarks |

## Engineering benchmark with no fixed competitor score

[VectorDBBench](https://github.com/zilliztech/vectordbbench) is a harness, not a
paper leaderboard. Its index-build time, ANN recall, latency, throughput, and QPS
depend on the pinned dataset, vector dimension/count, index parameters, client,
server, concurrency, and hardware. The repository was checked at
`cda6227206fe9ffaa742fe366de1fcac224c8018` (2026-07-15). A VidXP result should
publish its full configuration and may compare same-machine systems; no external
fixed score should be copied into the paper.

## How to use these numbers

For each selected benchmark, report three separate bands:

1. VidXP and other frozen/off-the-shelf methods on the exact same harness.
2. Simple controls such as random ranking, single-modality VidXP, and fixed
   fusion ablations.
3. Published trained systems as explicitly labeled context, never as if training
   conditions were equal.

A VidXP row belongs in a comparison table only when its run artifact records the
dataset version, split, evaluator commit, prediction file, model identifiers,
aggregation or fusion rule, and unsupported channels. No score in this document
is itself a VidXP result.
