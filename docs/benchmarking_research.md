# Defensible benchmarking methodology for VidXP

Current benchmarking index: [Benchmarking research](benchmarking/README.md)

> **Legacy draft — not the current plan.** This file contains an earlier,
> unsupported assumption that Urdu is an intentional project requirement and
> prematurely proposes a new local corpus. Do not use those passages as project
> scope. Use the [current benchmarking index](benchmarking/README.md) for active
> status and the [published benchmark catalog](benchmarking/benchmark_catalog.md)
> for the verified candidate research.

This document proposes a paper-ready evaluation protocol for the current VidXP pipeline: WhisperX + `all-MiniLM-L6-v2` dialogue retrieval, CLIP text-to-scene retrieval, and `face_recognition`-based actor clustering. It also inventories published reference results; it contains no new VidXP measurements.

## Related systems, datasets, and published reference points

### Comparability rule

No located public benchmark evaluates the same combined task as VidXP: Urdu speech is automatically transcribed, natural-language queries retrieve a point timestamp from long videos, visual scenes are indexed frame-by-frame, and unknown actors are clustered without identity supervision. Published numbers below must therefore be treated as **external reference points**, not as head-to-head results.

Use three comparability labels in the paper:

- **Closest system analogue:** overlaps several VidXP modalities or its corpus-search architecture, but differs in language, supervision, retrieval unit, corpus, or hardware. Its numbers give context only.
- **Component-level reference:** evaluates one constituent capability, such as whole-video CLIP retrieval, ASR alignment, or face verification. It cannot establish VidXP end-to-end quality.
- **Directly comparable:** identical frozen corpus, queries, relevance judgments, output unit, metrics, and hardware/software protocol. None of the published results inventoried here meet this condition; only baselines rerun through VidXP's proposed harness can be called directly comparable.

This distinction matters especially for Recall@K. Whole-video retrieval asks whether the correct clip is in the ranked list, video-corpus moment retrieval asks whether a returned **interval** overlaps a gold interval above a tIoU threshold, and VidXP currently returns a **point** scored with a time tolerance. The percentages have similar names but different denominators and relevance predicates.

### Closest end-to-end and multimodal systems

| Work | What it evaluates | Published result | Comparability to VidXP |
|---|---|---|---|
| [WISE (SIGIR 2026)](https://arxiv.org/abs/2602.12819) | An open-source engine spanning scene, object, face, acoustic-event, ASR-speech, metadata, and composite search. Its example pipeline uses sampled video frames, OpenCLIP, WhisperX, InsightFace, and Faiss. | The paper reports deployments on 55 million Wikimedia images and more than 6,000 hours of BBC video. It states that a one-hour video can be processed in under 10 minutes on a modern GPU and search over large collections completes in under one second. | **Closest system analogue.** The modality coverage and architecture are unusually close. However, the paper is a system/case-study report, not a judged retrieval benchmark; the timing claims do not define an VidXP-identical corpus, hardware, model configuration, or relevance set. Do not use them as a quality baseline or a speedup claim. |
| [ContextIQ (WACV 2025)](https://openaccess.thecvf.com/content/WACV2025/html/Chaubey_ContextIQ_A_Multimodal_Expert-Based_Video_Retrieval_System_for_Contextual_Advertising_WACV_2025_paper.html) | Zero-shot whole-video/clip retrieval combining video, audio, transcript, and metadata experts. | On MSR-VTT 1kA after randomly selecting one caption per video, ContextIQ reports P@1 81.7, P@5 59.1, R@5 93.7, and MAP@5 83.2; LanguageBind reports 85.5, 66.6, 97.7, and 86.6. On 600 one-minute Condensed Movies clips and 29 generated queries judged by three validators, ContextIQ reports 96.6, 88.3, 100, and 94.4; TwelveLabs reports 96.6, 90.3, 100, and 95.6. | **Closest multimodal effectiveness analogue, not direct.** It retrieves whole clips rather than timestamps, uses English queries, has custom metric definitions, randomly samples MSR-VTT captions, and uses a small custom judged movie protocol. |
| [XML / TVR (ECCV 2020)](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123660443.pdf) | Corpus-wide moment retrieval over video plus supplied subtitles on TVR: 109,000 queries over 21,800 TV clips from six shows. | On TVR validation with 100 candidate videos, XML reports tIoU 0.5 R@1/5/10/100 of 5.28/11.73/15.90/36.16 and tIoU 0.7 scores of 2.62/6.39/9.05/22.47. In the paper's [one-million-video scaling experiment](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123660443-supp.pdf), 100 queries at tIoU 0.7 give R@1 3.25 and R@5 8.71, with 29 seconds feature time, 76 GB features, and 0.005 seconds retrieval time on an RTX 2080 Ti and 40-core Xeon Silver 4114. | **Closest timestamp-retrieval benchmark, not direct.** XML is trained on English TVR and receives reference subtitles; it returns intervals and searches a much larger corpus. VidXP performs Urdu ASR first and currently returns points. |
| [HERO (EMNLP 2020)](https://aclanthology.org/2020.emnlp-main.161/) | Pretrained hierarchical video-plus-subtitle model evaluated on TVR retrieval/localization tasks. | On TVR validation at tIoU 0.7, the pretrained model reports video-corpus moment retrieval R@1/10/100 of 5.13/16.26/24.55, video retrieval of 30.11/62.69/87.78, and single-video moment retrieval of 4.02/10.38/62.93. Without pretraining, corpus moment R@1/10/100 is 2.98/10.65/18.25. | **Component/architecture reference.** It is trained and pretrained, uses supplied English subtitles, returns intervals, and does not include VidXP's ASR or actor clustering. |

WISE is the most appropriate related-system citation in a qualitative architecture comparison. TVR/XML is the most defensible external reference for corpus-wide temporal retrieval methodology. ContextIQ is useful for demonstrating multimodal expert fusion, but its unusually high values should never share a “higher/lower is better” column with VidXP's \(R@K,\delta\).

### Visual retrieval reference points

[CLIP4Clip](https://arxiv.org/abs/2104.08860) is a useful component reference because VidXP also builds on CLIP. On the MSR-VTT 9,000-train/1,000-test split, its zero-shot frame-aggregation baseline, CLIP-straight, reports text-to-video R@1/5/10 of **31.2/53.7/64.2** and median rank 4. After supervised fine-tuning, CLIP4Clip mean pooling reports **43.1/70.4/80.8**, while its sequential Transformer reports **44.5/71.4/81.6**. These are whole-video English-caption results on short clips, not long-video point localization results. The fine-tuned values are particularly unsuitable as an VidXP baseline unless that model is retrained and evaluated on the identical VidXP corpus.

For benchmark design, these public datasets cover complementary failure modes:

| Dataset | Scale and task | Recommended use | Limitation for this paper |
|---|---|---|---|
| [TVR](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123660443.pdf) | 109K queries, 21.8K TV clips, video/subtitle/both query labels, corpus-wide moment intervals | Best public analogue for dialogue-plus-vision temporal retrieval and tIoU conventions | English, supplied subtitles, copyrighted TV source requirements |
| [mTVR](https://aclanthology.org/2021.acl-short.92/) | 218K paired English/Chinese queries over the same 21.8K clips | Evidence for reporting each language separately and for multilingual retrieval design | Chinese and English only; it does not validate Urdu |
| [TVR-Ranking](https://arxiv.org/abs/2407.06597) | 94,442 graded query-moment judgments for imprecise queries | Use its graded-relevance idea only if VidXP collects multiple relevance levels; then add NDCG | VidXP's proposed binary interval/tolerance judgments do not support NDCG without new annotation |
| [VERIFIED (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/hash/477929b8d45ab759795b7aac94329b08-Abstract-Datasets_and_Benchmarks_Track.html) | Fine-grained Charades-FIG, DiDeMo-FIG, and ActivityNet-FIG moment-retrieval sets with partially matching candidates | Source of hard-negative design for objects, interactions, and subtly different actions | Generated/filtered English descriptions and different source distributions |
| [MSR-VTT](https://www.microsoft.com/en-us/research/publication/msr-vtt-a-large-video-description-dataset-for-bridging-video-and-language/) | 10K web clips and 200K captions for video-text retrieval | Optional component sanity check for VidXP's CLIP scene encoder | Short whole-video retrieval, not timestamp localization or Urdu entertainment video |

The preferred paper strategy is therefore to make the new frozen Urdu/entertainment corpus the primary evaluation and optionally run a clearly labeled public-dataset **component sanity check**. Do not merge scores across public and local datasets.

### Dialogue and face component references

WhisperX reports English ASR/alignment component results rather than Urdu semantic retrieval. Its published experiments include TED-LIUM WER 9.7 and a word-alignment comparison using a 200 ms collar; those values are useful for confirming an implementation, not as an expected VidXP Urdu WER. VidXP must report Urdu WER/CER on its own manually transcribed test audio because both language and compute configuration differ.

The `face_recognition` project states **99.38% accuracy on Labeled Faces in the Wild** for its underlying dlib recognition model ([official repository](https://github.com/ageitgey/face_recognition)). This is a face-verification number on still-image pairs, not a clustering result. It says nothing about missed profile/occluded faces, online cluster fragmentation, false merges, or the unknown number of identities in a video. The VidXP paper should mention it only as provenance for the embedding model, never as VidXP actor-search accuracy. A valid actor result must come from the track-level B-cubed protocol below, with detection coverage reported separately.

### How to present external numbers

Use a separate “published context” table, followed by the paper's own “same-harness baselines” table. The latter should contain only methods rerun on the identical frozen VidXP test set, for example:

- dialogue: random, exact lexical/BM25, reference-transcript oracle, and full WhisperX pipeline;
- scene: random, CLIP with each pre-registered time-based sample rate, and any temporal aggregation variant;
- actors: all-singleton, all-in-one, fixed 0.55 threshold, and development-tuned threshold.

State underneath the published-context table: **“Values are reproduced from their source papers and are not directly comparable to VidXP because datasets, languages, retrieval units, supervision, relevance definitions, and hardware differ.”** This prevents a literature-review table from being mistaken for an empirical leaderboard.

## 1. Freeze the evaluation task before running it

Use an immutable, versioned corpus and publish a manifest containing video IDs, checksums, durations, frame rates, resolutions, audio conditions, genres, and split membership. Split by source title/episode/film, never by frames or short clips from the same source. This avoids near-duplicate scenes, voices, and actors leaking across development and test sets.

Use three disjoint splits:

- **Development:** select face threshold, five-word phrase length, scene sample rate, temporal de-duplication, similarity settings, and any rejection threshold.
- **Test:** run once after the protocol and parameters are frozen.
- **Optional training:** only if a component is trained or fine-tuned. VidXP's present models are otherwise evaluated zero-shot.

The test corpus should cover at least clean/noisy speech, music under dialogue, overlapping speakers, indoor/outdoor scenes, fast and slow cuts, frontal/profile/occluded faces, lighting variation, and both major and minor actors. Report hours, titles, dialogue events, scene events, unique identities, and annotated face tracks per split.

The query set must be written without looking at system output. Have annotators mark **all** relevant temporal intervals, not only one preferred answer. Retain query-level strata:

- dialogue: verbatim Urdu, Urdu paraphrase, Roman Urdu, and English translation, but report each separately;
- scene: objects, actions, locations, visual attributes, and compositional descriptions;
- difficulty: unique target, repeated target, and hard-negative target with visually or semantically similar distractors.

This separation is essential because the official [`all-MiniLM-L6-v2` model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) labels the model as English and lists English training sources. A pooled “multilingual” number could hide failure on the principal Urdu use case. SBERT supports semantic search through independently encoded sentence embeddings and cosine similarity, but the original work does not establish Urdu performance ([Reimers and Gurevych, 2019](https://aclanthology.org/D19-1410/)).

## 2. Retrieval units and temporal relevance

Do not treat every indexed frame as a distinct relevant result. Consecutive frames from one event would otherwise inflate Recall@K and AP. Before scoring, convert ranked timestamps into distinct results with one frozen rule, such as shot segments or temporal non-maximum suppression with a fixed minimum gap. Apply the same rule to every system and baseline.

Let a gold temporal interval be \(g=[s_g,e_g]\), and a returned interval be \(r=[s_r,e_r]\). Temporal intersection-over-union is

\[
\operatorname{tIoU}(r,g)=
\frac{\max(0,\min(e_r,e_g)-\max(s_r,s_g))}
{\max(e_r,e_g)-\min(s_r,s_g)}.
\]

For interval-returning systems, result \(r\) is relevant at threshold \(\tau\) when
\(\max_g \operatorname{tIoU}(r,g)\geq\tau\). Report a pre-registered sweep such as
\(\tau\in\{0.3,0.5,0.7\}\), rather than selecting the best threshold after seeing test results. Text-to-video moment localization is conventionally formulated as retrieving a temporal segment from language ([Hendricks et al., 2017](https://openaccess.thecvf.com/content_iccv_2017/html/Hendricks_Localizing_Moments_in_ICCV_2017_paper.html)).

VidXP currently returns a **point timestamp**, for which IoU is undefined. For a point \(t\), define its distance to gold interval \(g\) as zero if \(t\in g\), otherwise the distance to the nearest boundary. A hit at tolerance \(\delta\) occurs when the minimum distance over gold intervals is at most \(\delta\). Report:

- hit rate at \(\delta\in\{0,1,2,5\}\) seconds;
- median and 90th-percentile temporal error;
- interval tIoU metrics only if VidXP is changed to return intervals.

For word alignment as a component diagnostic, the WhisperX paper defines a true positive as an exact word match whose predicted and reference segments overlap within a 200 ms collar, and reports precision and recall ([Bain et al., 2023, §3.2](https://arxiv.org/abs/2303.00747)). This 200 ms collar is suitable for **word-alignment evaluation**, not automatically for user-facing scene/speech search; search tolerances should reflect the declared navigation use case.

## 3. Retrieval metrics

The paper must state the exact convention because “Recall@K” has two incompatible uses.

### 3.1 Primary top-K success metric

For temporal moment retrieval, use

\[
R@K,\tau = \frac{1}{|Q|}\sum_{q\in Q}
\mathbb{1}\left[\max_{j\leq K,g\in G_q}\operatorname{tIoU}(r_{qj},g)\geq\tau\right].
\]

For point timestamps, replace the tIoU predicate with the \(\delta\)-tolerant hit predicate and label the result \(R@K,\delta\). This metric is the fraction of queries with at least one successful top-\(K\) answer; it is technically Success@K, although video-moment literature calls it \(R@K,\tau\). Use \(K\in\{1,5,10\}\). Do not describe it as set recall.

The conventional measure is rank-insensitive within the top \(K\) and binarizes localization at \(\tau\). The original AxIoU paper documents both limitations and proposes Average Max IoU as a rank- and overlap-sensitive complement ([Togashi et al., 2022](https://arxiv.org/abs/2203.16062)). If VidXP returns intervals, report mean AxIoU@5 or @10 in addition to \(R@K,\tau\).

### 3.2 MRR and mAP

For each query, let \(k_q\) be the rank of its first relevant distinct temporal event:

\[
\operatorname{MRR@K}=\frac{1}{|Q|}\sum_q
\begin{cases}
1/k_q,&k_q\leq K\\
0,&\text{otherwise}.
\end{cases}
\]

MRR is appropriate when a user needs one good jump point. The official TREC implementation defines reciprocal rank from the first relevant result and provides a reproducible reference implementation ([NIST `trec_eval`](https://github.com/usnistgov/trec_eval/blob/main/m_recip_rank.c)).

When a query has multiple distinct relevant occurrences, additionally report:

\[
\operatorname{AP@K}(q)=\frac{1}{|G_q|}
\sum_{k=1}^{K} P@k(q)\,\operatorname{rel}_q(k),\qquad
\operatorname{mAP@K}=\frac{1}{|Q|}\sum_q\operatorname{AP@K}(q).
\]

Match each returned event to at most one gold interval; repeated frames or segments matching an already matched gold event are non-relevant duplicates. The official TREC MAP implementation computes precision whenever a relevant result is retrieved and averages over the total number of relevant items ([NIST `trec_eval`](https://github.com/usnistgov/trec_eval/blob/main/m_map.c)). MAP is meaningful only when all relevant occurrences have been judged.

Also report the literal set recall
\(|\text{top-}K\cap G_q|/|G_q|\) only if multiple target occurrences are central to the claim; label it **set Recall@K** to distinguish it from \(R@K,\tau\).

Negative queries with no relevant event cannot be scored by MRR or AP. If VidXP is claimed to reject such queries, evaluate the score threshold separately with a precision-recall curve and a pre-selected development-set operating point. Otherwise state that the current nearest-neighbor interface always returns an answer.

## 4. Module-specific quality evaluation

### 4.1 Urdu dialogue pipeline

Report both component and end-to-end results:

1. **ASR:** WER and CER after publishing the exact Urdu normalization/tokenization rules. The Whisper paper explicitly notes that UTF-8 output makes text standardization consequential and uses WER/CER for multilingual evaluation ([Radford et al., 2022](https://cdn.openai.com/papers/whisper.pdf)).
2. **Alignment:** exact-word segment precision/recall with the WhisperX 200 ms collar.
3. **Search:** \(R@1/5/10,\delta\), MRR@10, and mAP@10 when the phrase occurs more than once.

Run two diagnostic variants with identical retrieval code:

- **reference-transcript oracle:** reference Urdu words and timestamps, isolating embedding/retrieval quality;
- **WhisperX transcript:** the actual end-to-end system.

Their gap estimates ASR/alignment propagation into retrieval. Add an exact-token or lexical retrieval baseline and a random ranking baseline. Keep Urdu script, Roman Urdu, and English queries in separate tables.

### 4.2 Text-to-scene retrieval

CLIP learns a joint image-text space from image-caption matching ([Radford et al., 2021](https://proceedings.mlr.press/v139/radford21a.html)), but VidXP indexes every frame. Score temporally de-duplicated events, not raw frame hits. Report:

- within-video and corpus-wide retrieval separately;
- \(R@1/5/10,\delta\) for the current point output;
- MRR@10 and, for repeated events, mAP@10;
- \(R@K,\tau\) and AxIoU only if interval proposals are returned.

Stratify results by query type and by event duration. Include random ranking and a simple metadata/time-prior-free baseline. Sweep frame sampling stride and report the quality/throughput/index-size trade-off; do not tune stride on test.

### 4.3 Actor clustering

Define one scoring item as an annotated **face track** (preferred), or as a face observation sampled at a fixed stride. Raw every-frame scoring overweights long, static shots. State whether identities are clustered within each video or across the whole corpus; the current design should be evaluated within video unless cross-video identity consistency is explicitly implemented.

Measure face detection coverage separately: matched annotated tracks/detections, misses, and false positives. For clustering-only scores, use the same set of one-to-one matched face items in truth and prediction.

Use B-cubed as the primary clustering measure. For item \(i\), predicted cluster \(C(i)\), and true identity class \(L(i)\):

\[
P_i=\frac{|C(i)\cap L(i)|}{|C(i)|},\qquad
R_i=\frac{|C(i)\cap L(i)|}{|L(i)|}.
\]

Average \(P_i\) and \(R_i\) over items, and take their harmonic mean for B-cubed F1. B-cubed was introduced by Bagga and Baldwin ([1998](https://aclanthology.org/C98-1012/)); a formal comparison found it satisfies homogeneity, completeness, rag-bag, and cluster-size constraints, while warning that pair-counting metrics have a quadratic cluster-size effect ([Amigó et al., 2009](https://doi.org/10.1007/s10791-008-9066-8)).

Report these secondary metrics:

- **Pairwise precision/recall/F1:** TP means a pair is together in both predicted and true partitions; FP is together only in prediction; FN is together only in truth. Useful for interpreting false merges versus fragmentation, but dominated by large identities.
- **Adjusted Rand Index (ARI):** chance-adjusted pair agreement, invariant to cluster label permutation. Use the standard Hubert-Arabie implementation and state the library version ([scikit-learn documentation](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.adjusted_rand_score.html)).
- predicted/true cluster count ratio, singleton rate, and per-identity fragmentation count.

Report a macro average over videos plus the pooled result. Compare the fixed threshold 0.55 with a development-tuned threshold; include all-singleton and all-in-one-cluster baselines. Because VidXP updates face prototypes chronologically, preserve the real chronological frame order and document it.

## 5. Efficiency, latency, throughput, and resources

Measure both **user-visible end-to-end** time and component time. MLPerf defines latency from query issue to reply and treats preprocessing/postprocessing as part of a run unless explicitly exempted; it also distinguishes single-stream latency from offline throughput ([MLPerf Inference Rules](https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc)). Adopt the same clarity without claiming MLPerf compliance.

### 5.1 Required measurements

For indexing, report:

- total wall time and real-time factor \(RTF=\text{wall seconds}/\text{video seconds}\);
- processed video minutes per wall-clock minute and frames/s;
- stage times: audio extraction, WhisperX transcription, alignment, speech embedding/write, video decode, CLIP embedding/write, face detection/encoding/clustering/write;
- final database/index bytes.

For queries, report separately:

- **cold start:** process start through model/database loading and first complete result; model downloads are excluded and reported separately;
- **warm core query:** text preprocessing/embedding, vector search, and result conversion;
- **warm end-to-end CLI:** invocation through printed/returned timestamp;
- sequential throughput in queries/s at fixed corpus sizes.

Run query benchmarks at multiple indexed-corpus scales and video lengths. Report median, IQR, and p95 latency; report p99 only with enough observations to estimate it credibly. For expensive indexing, use at least five independent process runs per condition. For warm query latency, use at least ten unmeasured warm-ups followed by at least 30 measured repetitions per query/condition, increasing repetitions if variability remains high.

PyTorch's benchmark utility performs warm-ups, fixes thread-pool size, synchronizes asynchronous accelerators, collects replicates, and recommends the median because it is more robust to run-to-run variation ([PyTorch benchmark documentation](https://docs.pytorch.org/docs/stable/benchmark_utils.html)). If timing manually on an accelerator, synchronize immediately before starting and before stopping; otherwise only kernel launch time may be measured ([PyTorch benchmark recipe](https://docs.pytorch.org/tutorials/recipes/recipes/benchmark.html)).

### 5.2 Resources and reproducibility

Record peak:

- process resident memory (RSS);
- accelerator tensor memory, after resetting peak statistics;
- GPU utilization and device memory over time where available;
- CPU utilization, disk reads/writes, and index size.

PyTorch's `max_memory_allocated()` returns the peak tensor memory since program start and documents resetting peak statistics between stages ([official API](https://docs.pytorch.org/docs/stable/generated/torch.cuda.memory.max_memory_allocated.html)). Python exposes maximum RSS through `resource.getrusage` ([official documentation](https://docs.python.org/3/library/resource.html)); NVIDIA documents sampled GPU utilization and frame-buffer memory semantics in `nvidia-smi` ([official documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html)).

For every result table, report CPU model/core count, RAM, storage, GPU and VRAM, OS, accelerator driver/runtime, Python and dependency versions, model identifiers/hashes, precision/compute type, batch size, thread counts, input resolution/fps, effective scene sample rate, actor frame stride, database settings, and Git commit. Fix seeds where randomness exists, keep the machine otherwise idle, randomize condition order, and publish raw per-run measurements and the benchmark script.

## 6. Aggregation, uncertainty, and comparisons

- Make query-macro averages the primary retrieval results so long videos or frequent phrases do not dominate.
- Also show per-video results and failure strata.
- Report 95% confidence intervals by resampling the highest independent unit—source title/video, not adjacent frames or queries from the same event. Use the same resampled units for paired system comparisons.
- Publish per-query/per-video scores so alternative aggregation is possible.
- Compare systems on the identical frozen queries and judgments.
- Present quality and efficiency together: e.g. \(R@1\) versus RTF for Whisper model/compute type, scene sample rate, and face threshold/history choices.

## 7. Minimum paper table set

1. Corpus and annotation statistics by split and query stratum.
2. Dialogue ASR/alignment results, then end-to-end retrieval with reference-transcript oracle and lexical baseline.
3. Scene retrieval by query type, within-video versus corpus-wide, with sample-rate ablation.
4. Actor detection coverage plus B-cubed P/R/F1, pairwise P/R/F1, ARI, and cluster-count diagnostics.
5. Indexing RTF/stage breakdown, query cold/warm p50/p95 and QPS, peak RAM/accelerator memory, and index size.
6. Paired 95% confidence intervals and qualitative failure examples selected by a fixed rule (for example, worst five test queries per module), not hand-picked successes.

The most important implementation prerequisite is to expose top-\(K\) ranked results and stable event IDs in the benchmark harness. The product may continue returning only top-1, but a top-1-only interface cannot support MRR, mAP, or \(R@5/10\).
