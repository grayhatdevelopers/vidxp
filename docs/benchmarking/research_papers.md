# Relevant research-paper inventory

Collection index: [Benchmarking research](README.md)

Status: Paper-level benchmark-use audit complete; reading queue active

Last verified: 2026-07-26

Related decision record: [Published benchmark catalog](benchmark_catalog.md)

This inventory contains papers that introduce a serious candidate benchmark,
establish an evaluation protocol, or provide a close baseline for an implemented
VidXP component. It is deliberately prioritized: “relevant” does not mean every
paper mentioning video retrieval, CLIP, ASR, or face recognition.

The paper-writing team can review these later. This workstream's immediate use is
to trace which datasets, metrics, baselines, and public artifacts each paper
actually relies on.

## Reading order

Start with these papers before reviewing individual model variants:

1. **TVR / XML** for the closest peer-reviewed corpus-level visual/transcript
   temporal-retrieval task.
2. **Localizing Moments in Video with Natural Language** for the simplest
   executable visual moment benchmark.
3. **QVHighlights / Moment-DETR** for modern interval and highlight evaluation.
4. **Zero-shot Video Moment Retrieval With Off-the-Shelf Models** for the closest
   methodological comparison to VidXP's untuned CLIP retrieval.
5. **HiREST** and **QuerYD** for speech-backed retrieval options.
6. **BCL** for unknown-number video face clustering and its WCP/NMI protocol.
7. **VPCD** and **C1C** for stronger person/track constraints and dataset context.
8. **LongVALE** for the strongest peer-reviewed combined vision–audio–speech
   temporal benchmark.
9. **Towards a Complete Benchmark on Video Moment Localization** for cross-dataset
   bias and evaluation methodology.

## Multimodal and whole-system retrieval

| Paper | Venue/year | Benchmarks introduced or used | Why it belongs |
| --- | --- | --- | --- |
| [TVR: A Large-Scale Dataset for Video-Subtitle Moment Retrieval](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123660443.pdf) | ECCV 2020 | Introduces TVR; VCMR, SVMR, VR | Closest established joint video/subtitle temporal benchmark |
| [HERO: Hierarchical Encoder for Video+Language Omni-representation Pre-training](https://aclanthology.org/2020.emnlp-main.161/) | EMNLP 2020 | Introduces How2R/How2QA; evaluates TVR, TVQA, How2R, How2QA, VIOLIN | Major video-plus-subtitle representation baseline and defining How2 retrieval source |
| [CONQUER: Contextual Query-aware Ranking for Video Corpus Moment Retrieval](https://arxiv.org/abs/2109.10016) | ACM MM 2021 | TVR and DiDeMo | Corpus moment-ranking comparator |
| [ReLoCLNet: Video Corpus Moment Retrieval with Contrastive Learning](https://26hzhang.github.io/publication/reloclnet/) | SIGIR 2021 | TVR and ActivityNet Captions | Efficient separately encoded VCMR/SVMR reference; the earlier DiDeMo claim was incorrect |
| [TVR-Ranking: A Dataset for Ranked Video Moment Retrieval with Imprecise Queries](https://arxiv.org/abs/2407.06597) | SIGIR-AP 2025 | Introduces graded TVR-Ranking | Best graded ranked-search definition, with inherited TVR access limits |
| [Finding Moments in Video Collections Using Natural Language](https://arxiv.org/abs/1907.12763) | arXiv 2019 | Corpus-converted DiDeMo and Charades-STA; introduces STAL, with CAL naming used in the repository | Establishes the video-corpus moment-retrieval lineage on which TVR/XML builds |
| [VRAgent: Self-Refining Agent for Zero-Shot Multimodal Video Retrieval](https://openaccess.thecvf.com/content/WACV2026/html/Shah_VRAgent_Self-Refining_Agent_for_Zero-Shot_Multimodal_Video_Retrieval_WACV_2026_paper.html) | WACV 2026 | MM-MSRVTT and TVR-1200 | New visual/transcript/joint retrieval reference; official paper/supplement checked, but no public annotations, evaluator, predictions, or implementation found |
| [SAVE: Speech-Aware Video Representation Learning for Video-Text Retrieval](https://openaccess.thecvf.com/content/CVPR2026/html/Zhao_SAVE_Speech-Aware_Video_Representation_Learning_for_Video-Text_Retrieval_CVPR_2026_paper.html) | CVPR 2026 | MSR-VTT-9k/7k, VATEX, Charades, LSMDC | Speech-aware whole-video retrieval, but not timestamp localization |
| [LongVALE](https://openaccess.thecvf.com/content/CVPR2025/papers/Geng_LongVALE_Vision-Audio-Language-Event_Benchmark_Towards_Time-Aware_Omni-Modal_Perception_of_Long_Videos_CVPR_2025_paper.pdf) | CVPR 2025 | Introduces LongVALE | Strongest peer-reviewed vision–audio–speech temporal target; no actor task |
| [MultiVENT 2.0](https://openaccess.thecvf.com/content/CVPR2025/papers/Kriz_MultiVENT_2.0_A_Massive_Multilingual_Benchmark_for_Event-Centric_Video_Retrieval_CVPR_2025_paper.pdf) | CVPR 2025 | Introduces MultiVENT 2.0 | Large-corpus visual, speech/ASR, embedded-text/OCR, and description-metadata retrieval; whole videos rather than moments |
| [MMMORRF: Multimodal Multilingual Modularized Reciprocal Rank Fusion](https://doi.org/10.1145/3726302.3730157) | SIGIR 2025 | MultiVENT 2.0 and TVR | Direct frames+OCR+ASR fixed-fusion pipeline comparator with exact full-test results |
| [MAGMaR Shared Task System Description: Video Retrieval with OmniEmbed](https://arxiv.org/abs/2506.09409) | arXiv/MAGMaR presentation 2025 | MultiVENT 2.0 official shared-task test | Provides zero-shot and target-trained unified-embedding results plus a released checkpoint; no archival workshop paper was found |
| [Q2E: Query-to-Event Decomposition for Zero-Shot Multilingual Text-to-Video Retrieval](https://aclanthology.org/2025.ijcnlp-long.121/) | IJCNLP-AACL 2025 | Original MultiVENT, MSR-VTT 1k-A, and MSVD | Training-free visual/Whisper rank-fusion comparator; the original MultiVENT protocol must not be conflated with MultiVENT 2.0 |
| [MUVR](https://papers.neurips.cc/paper_files/paper/2025/hash/2a80c10b1fd6a6488a96cc1f4fbacc84-Abstract-Datasets_and_Benchmarks_Track.html) | NeurIPS Datasets & Benchmarks 2025 | Introduces MUVR | Paired query-video plus detailed-text retrieval; pure-text and pure-video are ablations, not separate defining query types |
| [FLARE](https://arxiv.org/abs/2605.10228) | arXiv 2026 | Introduces FLARE | Closest downloadable visual/audio/joint retrieval set; preprint and query-selection caveats |
| [TRECVID Ad-hoc Video Search overview](https://trec.nist.gov/pubs/trec33/papers/Overview_avs_vtt_actev.pdf) | TRECVID 2024 | V3C master-shot retrieval | Established large-corpus visual retrieval and xinfAP protocol |
| [V3C – A Research Video Collection](https://arxiv.org/abs/1810.04401) | MMM 2019 | Introduces the V3C corpus family | Dataset-defining source for V3C scale, shot construction, and item-level Creative Commons licensing used by TRECVID AVS |
| [SAVA: Search and Anchoring in Video Archives](https://ceur-ws.org/Vol-1436/Paper11.pdf) | MediaEval 2015 | BBC spoken-plus-visual interval retrieval | Historically close combined protocol; present data portability is not verified |

## Closest implemented systems

These papers are architectural and engineering comparators. They do not provide a
portable judged benchmark covering all of VidXP.

| Paper/system | Venue/year | Overlap | Benchmark status |
| --- | --- | --- | --- |
| [Multi-modal Video Search by Examples: A Video Quality Impact Analysis](https://pure.ulster.ac.uk/ws/files/222412425/IET_Computer_Vision_-_2024_-_Wu_-_Multi_modal_video_search_by_examples_A_video_quality_impact_analysis.pdf) | IET Computer Vision 2024 | Faces, scenes, speakers, ASR, fusion, approximate search over BBC video | Closest functional analogue; BBC data and judgments are not portable |
| [WISE: A Multimodal Search Engine for Visual Scenes, Audio, Objects, Faces, Speech, and Metadata](https://www.robots.ox.ac.uk/~vgg/publications/2026/sridhar2026wise/) | SIGIR 2026 | Scene/object/face, acoustic event, WhisperX speech, metadata, composite queries | Open-source system; deployments and latency context, no portable judged protocol |
| [ContextIQ](https://openaccess.thecvf.com/content/WACV2025/html/Chaubey_ContextIQ_A_Multimodal_Expert-Based_Video_Retrieval_System_for_Contextual_Advertising_WACV_2025_paper.html) | WACV 2025 | Video, audio, transcript, and metadata experts | Whole-video reference; supplemental annotations but no public implementation |
| [Collaborative Experts](https://www.robots.ox.ac.uk/~vgg/research/collaborative-experts/) | BMVC 2019 | Appearance, motion, scene, ASR, OCR, audio experts | Public models/features and corrected results; whole-video task |
| [Multi-Modal Transformer for Video Retrieval](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123490205.pdf) | ECCV 2020 | RGB, motion, scene, face, OCR, speech, audio experts | Multi-stream whole-video retrieval context |
| [MDMMT](https://openaccess.thecvf.com/content/CVPR2021W/HVU/html/Dzabraev_MDMMT_Multidomain_Multimodal_Transformer_for_Video_Retrieval_CVPRW_2021_paper.html) | CVPR Workshop 2021 | Multi-domain, multi-modal video retrieval | Model comparator across established short-video datasets |
| [Everything at Once](https://openaccess.thecvf.com/content/CVPR2022/html/Shvetsova_Everything_at_Once_-_Multi-Modal_Fusion_Transformer_for_Video_Retrieval_CVPR_2022_paper.html) | CVPR 2022 | YouCook2/MSR-VTT retrieval; CrossTask/Mining YouTube localization | Historical audiovisual/zero-shot comparator; HowTo100M is pretraining, not evaluation |
| [EclipSE: Efficient Long-Range Video Retrieval Using Sight and Sound](https://www.ecva.net/papers/eccv_2022/papers_ECCV/papers/136940405.pdf) | ECCV 2022 | ActivityNet, QVHighlights, YouCook2, DiDeMo, Charades | Direct long-video audiovisual and efficiency comparator; several datasets are repurposed as paragraph-to-video retrieval |
| [TEFAL: Audio-Enhanced Text-to-Video Retrieval using Text-Conditioned Feature Alignment](https://openaccess.thecvf.com/content/ICCV2023/html/Ibrahimi_Audio-Enhanced_Text-to-Video_Retrieval_using_Text-Conditioned_Feature_Alignment_ICCV_2023_paper.html) | ICCV 2023 | MSR-VTT-7K/9K, LSMDC, VATEX, Charades | Accuracy-oriented audio-conditioned comparator; no official implementation located |
| [AVIGATE: Learning Audio-guided Video Representation with Gated Attention for Video-Text Retrieval](https://openaccess.thecvf.com/content/CVPR2025/html/Jeong_Learning_Audio-guided_Video_Representation_with_Gated_Attention_for_Video-Text_Retrieval_CVPR_2025_paper.html) | CVPR 2025 | MSR-VTT-9K, VATEX, Charades | Highest-value efficient generic-audio gating comparator; trained per dataset |
| [VAST](https://proceedings.neurips.cc/paper_files/paper/2023/file/e6b2b48b5ed90d07c305932729927781-Paper-Conference.pdf) | NeurIPS 2023 | Vision–audio–subtitle representation | Retrieval/caption/QA model, not actor or native long-video search |
| [VALOR](https://arxiv.org/abs/2304.08345) | IEEE TPAMI 47(2), 2025; online 2024 | Introduces VALOR-32K; also uses standard retrieval, captioning, and QA sets | Benchmark-defining audiovisual-caption dataset plus broad representation reference |
| [CLaMR](https://arxiv.org/abs/2506.06144) | arXiv 2025 | Frame, ASR, OCR, and metadata late-interaction retrieval on MultiVENT/MSR-VTT | Relevant large-corpus fusion baseline; not peer-reviewed as verified |
| [Video-ColBERT: Contextualized Late Interaction for Text-to-Video Retrieval](https://openaccess.thecvf.com/content/CVPR2025/html/Reddy_Video-ColBERT_Contextualized_Late_Interaction_for_Text-to-Video_Retrieval_CVPR_2025_paper.html) | CVPR 2025 | MSR-VTT, MSVD, VATEX, DiDeMo, ActivityNet | Direct vision-text late-interaction context for CLaMR; no audio/transcript evaluation |

## Dialogue, transcript, and speech retrieval

| Paper or evaluation | Venue/year | Benchmark relationship | Review purpose |
| --- | --- | --- | --- |
| [HiREST: Hierarchical Video-Moment Retrieval and Step-Captioning](https://openaccess.thecvf.com/content/CVPR2023/papers/Zala_Hierarchical_Video-Moment_Retrieval_and_Step-Captioning_CVPR_2023_paper.pdf) | CVPR 2023 | Introduces HiREST | Same Whisper plus MiniLM family as VidXP; practical speech-backed instructional baseline rather than conversational dialogue |
| [QuerYD: A Video Dataset with High-Quality Text and Audio Narrations](https://arxiv.org/abs/2011.11071) | ICASSP 2021 | Introduces QuerYD/QuerYDSegments | Paragraph video retrieval and oracle-proposal segment ranking over separate audio descriptions, not unrestricted boundary prediction |
| [TREC 2020 Podcasts Track Overview](https://trec.nist.gov/pubs/trec29/papers/OVERVIEW.P.pdf) | TREC 2020 | Podcast segment retrieval with graded judgments | Strong semantic spoken-content protocol; corpus distribution is closed |
| [OpenKWS 2013 Evaluation Plan](https://www.nist.gov/document/openkws13-evalplan-v4pdf) | NIST 2013 | Babel keyword search | Formal occurrence matching, ATWV, thresholds, indexing/search timing |
| [WhisperX: Time-Accurate Speech Transcription of Long-Form Audio](https://www.isca-archive.org/interspeech_2023/bain23_interspeech.html) | Interspeech 2023 | TED-LIUM timing/transcription experiments | Provenance and limitations of the alignment component; not a retrieval benchmark |
| [The MGB Challenge](https://www.cstr.ed.ac.uk/downloads/publications/2015/bell15_mgb_challenge.pdf) | ASRU 2015 | Broadcast ASR, diarization, and subtitle alignment | Speech-component evaluation lineage, not current end-to-end task |
| [mTVR: Multilingual Moment Retrieval in Videos](https://aclanthology.org/2021.acl-short.92/) | ACL-IJCNLP 2021 | Multilingual extension of TVR | Hold as reference; multilingual evaluation is not an intentional current claim |

Historical MediaEval Search & Hyperlinking and Rich Speech Retrieval work should be
reviewed for evaluation lineage, but the BBC licensing and old challenge
infrastructure make it unsuitable as the first executable benchmark.

## Visual moment and scene retrieval

| Paper | Venue/year | Benchmarks introduced or used | Why it belongs |
| --- | --- | --- | --- |
| [Localizing Moments in Video with Natural Language](https://arxiv.org/abs/1708.01641) | ICCV 2017 | Introduces DiDeMo | Defines the simplest first visual test and its 21-moment evaluator |
| [Moment-DETR: End-to-End Video Moment Retrieval with Natural Language](https://proceedings.neurips.cc/paper/2021/hash/62e0973455fd26eb03e91d5741a4a3bb-Abstract.html) | NeurIPS 2021 | Introduces QVHighlights | Primary modern interval/saliency benchmark |
| [Zero-shot Video Moment Retrieval With Off-the-Shelf Models](https://proceedings.mlr.press/v203/diwan23a.html) | Transfer Learning for NLP Workshop, PMLR 2023 | QVHighlights filtered validation set (1,434 videos) | Nearest zero-shot comparison, but its shot proposals and watershed merging go beyond raw frame-level CLIP scoring |
| [TALL: Temporal Activity Localization via Language Query](https://arxiv.org/abs/1705.02101) | ICCV 2017 | Introduces Charades-STA | Established, relatively manageable known-video interval benchmark |
| [Towards a Complete Benchmark on Video Moment Localization](https://proceedings.mlr.press/v238/chae24a.html) | AISTATS 2024 | ActivityNet Captions, Charades-STA, DiDeMo, TACoS, YouCook2, MSR-VTT, TVR; MoLEF framework | Cross-dataset bias, cost, and benchmark-methodology review; not a new dataset or zero-shot baseline |
| [QD-DETR: Query-Dependent Video Representation for Moment Retrieval and Highlight Detection](https://github.com/wjun0830/QD-DETR) | CVPR 2023 | QVHighlights, Charades-STA, TVSum | Supervised moment/highlight comparator; no experimental Ego4D, TACoS, DiDeMo, MSR-VTT, or ActivityNet result |
| [UniVTG: Towards Unified Video-Language Temporal Grounding](https://github.com/showlab/UniVTG) | ICCV 2023 | QVHighlights, Ego4D NLQ, Charades-STA, TACoS, YouTube Highlights, TVSum, QFVS | Broad pretrained/supervised temporal-label comparator; only explicitly marked rows are zero-shot |
| [VERIFIED: A Video Corpus Moment Retrieval Benchmark for Fine-Grained Video Understanding](https://proceedings.neurips.cc/paper_files/paper/2024/hash/477929b8d45ab759795b7aac94329b08-Abstract-Datasets_and_Benchmarks_Track.html) | NeurIPS Datasets & Benchmarks 2024 | Introduces Charades-FIG, DiDeMo-FIG, ActivityNet-FIG for corpus moment retrieval | Major fine-grained VCMR robustness test with published baseline tables and released annotations/features; standalone code/evaluator and repository license remain incomplete |
| [LoVR: A Benchmark for Long Video Retrieval in Multimodal Contexts](https://arxiv.org/abs/2505.13928) | The Web Conference 2026 | Introduces bidirectional long-video and predefined scene-clip retrieval over 467 videos | Accepted benchmark with published zero-shot baselines and public data/code; released split metadata currently conflicts with the paper and must be pinned before execution |
| [MAD: A Scalable Dataset for Language Grounding in Videos from Movie Audio Descriptions](https://arxiv.org/abs/2112.00431) | CVPR 2022 | Introduces MAD | Long-film match, but raw movies are not distributed |
| [Ego4D](https://arxiv.org/abs/2110.07058) | CVPR 2022 | Includes Natural Language Queries | Strong long-video benchmark, deferred for access and compute cost |

## Whole-video CLIP-style retrieval

These papers are useful for scene-embedding baselines, but their headline task
ranks whole videos rather than locating timestamps.

| Paper | Typical benchmarks | Relevance |
| --- | --- | --- |
| [CLIP: Learning Transferable Visual Models From Natural Language Supervision](https://arxiv.org/abs/2103.00020) | Image-text transfer only; no selected video temporal benchmark | Foundation for the legacy CLIP scene baseline and a relevant SigLIP2 predecessor; provenance, not a video benchmark |
| [CLIP4Clip](https://github.com/ArrowLuo/CLIP4Clip) | MSR-VTT, MSVD, LSMDC, ActivityNet, DiDeMo whole-video/clip retrieval | Established CLIP video-text comparator; its DiDeMo/ActivityNet use is not temporal localization |
| [Frozen in Time](https://github.com/m-bain/frozen-in-time) | MSR-VTT, DiDeMo, LSMDC, MSVD whole-video retrieval | Retrieval baseline and practical MSR-VTT preparation route; WebVid/image-caption sets are pretraining |
| [X-CLIP](https://github.com/xuguohai/X-CLIP) | MSR-VTT, MSVD, LSMDC, DiDeMo, ActivityNet whole-video retrieval | Stronger supervised CLIP-style comparator, not a timestamp baseline |
| [MSR-VTT](https://www.microsoft.com/en-us/research/publication/msr-vtt-a-large-video-description-dataset-for-bridging-video-and-language/) | Introduces MSR-VTT | Corpus-ranking component benchmark, not temporal localization |

## Dataset-defining sources used by visual comparators

These papers are included because the audited comparator papers actually evaluate
on their datasets. Their original tasks are not automatically VidXP-compatible.

| Paper | Venue/year | Dataset/protocol defined | Why it belongs |
| --- | --- | --- | --- |
| [Dense-Captioning Events in Videos](https://arxiv.org/abs/1705.00754) | ICCV 2017 | Introduces ActivityNet Captions and dense event captioning | Defines the timestamps/descriptions later grounding papers reuse; a later moment-retrieval evaluator must still be selected |
| [Grounding Action Descriptions in Videos](https://aclanthology.org/Q13-1003/) | TACL 2013 | Introduces TACoS sentence-to-interval grounding | Defines the narrow procedural/cooking benchmark used by TALL, UniVTG, and MoLEF |
| [Collecting Highly Parallel Data for Paraphrase Evaluation](https://aclanthology.org/P11-1020/) | ACL 2011 | Introduces the MSVD/YouTube2Text corpus | Dataset provenance for later whole-video retrieval; the defining paper is not a temporal-retrieval paper |
| [Movie Description](https://arxiv.org/abs/1605.03705) | IJCV 2017 | Consolidates movie-description data and LSMDC challenge protocols | Defines movie-domain pre-segmented-clip retrieval context; copyright/registration constraints remain |
| [VaTeX: A Large-Scale, High-Quality Multilingual Dataset for Video-and-Language Research](https://openaccess.thecvf.com/content_ICCV_2019/html/Wang_VaTeX_A_Large-Scale_High-Quality_Multilingual_Dataset_for_Video-and-Language_Research_ICCV_2019_paper.html) | ICCV 2019 | Introduces VATEX captioning and translation | Required provenance for audiovisual comparators that adapt VATEX to retrieval; not a native temporal benchmark |
| [Hollywood in Homes: Crowdsourcing Data Collection for Activity Understanding](https://arxiv.org/abs/1604.01753) | ECCV 2016 | Introduces the base Charades dataset | Provenance/license source for Charades-STA; TALL defines the language-grounding extension |
| [YouCook2: Towards Diverse Procedure Step Captioning](https://arxiv.org/abs/1805.07395) | ECCV 2018 | Introduces YouCook2 procedure segments/captions | Required because MoLEF, EclipSE, and Everything at Once experimentally use YouCook2 under adapted protocols |

## Actor and video-face clustering

| Paper | Venue/year | Benchmarks introduced or used | Why it belongs |
| --- | --- | --- | --- |
| [“Hello! My name is... Buffy” – Automatic Naming of Characters in TV Video](https://www.bmva-archive.org.uk/bmvc/2006/papers/340.pdf) | BMVC 2006 | Introduces the early Buffy face-track naming data; evaluates episodes 05-02 and 05-05 | Benchmark provenance for later Buffy clustering protocols; evaluates naming precision/recall rather than unknown-K clustering |
| [“Knock! Knock! Who is it?” Probabilistic Person Identification in TV-Series](https://www.cs.toronto.edu/~makarand/papers/CVPR2012.pdf) | CVPR 2012 | Introduces/evaluates the first six BBT season-1 episodes | Benchmark provenance for BBT; evaluates supervised face/person identification, not BCL's later clustering protocol |
| [Accio: A Data Set for Face Track Retrieval in Movies Across Age](https://www.cs.toronto.edu/~makarand/papers/ICMR2015.pdf) | ICMR 2015 | Introduces Accio over all eight Harry Potter movies | Defines restricted/unrestricted within- and across-movie track-retrieval protocols with mAP and precision@K |
| [On Evaluating Face Tracks in Movies](https://doi.org/10.1109/ICIP.2013.6738618) | ICIP 2013 | Introduces Hannah | Defining paper for the full-movie tracking annotations and tracking-oriented evaluation |
| [Video Face Clustering With Unknown Number of Clusters](https://openaccess.thecvf.com/content_ICCV_2019/html/Tapaswi_Video_Face_Clustering_With_Unknown_Number_of_Clusters_ICCV_2019_paper.html) | ICCV 2019 | MovieGraphs for supervised train/validation; BBT and Buffy six-episode tests | Defines BCL's unknown-K WCP/NMI/cluster-count protocol; supplied features alone do not test VidXP embeddings |
| [Constrained Video Face Clustering using 1NN Relations](https://www.robots.ox.ac.uk/~vgg/research/c1c/src/VickyKalogeitonBMVC2020.pdf) | BMVC 2020 | BBT, Buffy, Sherlock, and introduced Friends | WCP/NMI at frame level; the standard Friends setup discards invalid and irrelevant/background tracks |
| [Face, Body, Voice: Video Person-Clustering with Multiple Modalities](https://openaccess.thecvf.com/content/ICCV2021W/CVEU/html/Brown_Face_Body_Voice_Video_Person-Clustering_With_Multiple_Modalities_ICCVW_2021_paper.html) | ICCV Workshop 2021 | Introduces and evaluates VPCD: TBBT, Buffy, Sherlock, Friends, Hidden Figures, About Last Night | Automatic-termination and oracle-cluster protocols with WCP, NMI, character precision, and character recall |
| [VideoClusterNet: Self-Supervised and Adaptive Face Clustering for Videos](https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/4432_ECCV_2024_paper.php) | ECCV 2024 | BBT, Buffy, and introduced MovieFaceCluster | WCP plus predicted-cluster ratio on nine movies; dataset link is presently unavailable |
| [Self-supervised Video-centralised Transformer for Video Face Clustering](https://arxiv.org/abs/2203.13166) | IEEE TPAMI 2023 | BBT and introduced EasyCom-Clustering | Known-K WCP/NMI and unknown-K cluster-count error/NMI; promised dataset remains unreleased |
| [Constrained Clustering and Its Application to Face Clustering in Videos](https://openaccess.thecvf.com/content_cvpr_2013/html/Wu_Constrained_Clustering_and_2013_CVPR_paper.html) | CVPR 2013 | BF0502 and Notting-Hill | Known-K, frame-level clustering accuracy; foundational cannot-link/must-link method, not an unknown-K protocol |
| [Simultaneous Clustering and Tracklet Linking for Multi-face Tracking in Videos](https://openaccess.thecvf.com/content_iccv_2013/html/Wu_Simultaneous_Clustering_and_2013_ICCV_paper.html) | ICCV 2013 | Frontal, Turning, and BBT01 | Face/tracklet clustering accuracy plus predicted tracks, mostly tracked, fragments, and identity switches |
| [End-To-End Face Detection and Cast Grouping in Movies Using Erdos-Renyi Clustering](https://openaccess.thecvf.com/content_iccv_2017/html/Jin_End-To-End_Face_Detection_ICCV_2017_paper.html) | ICCV 2017 | BBT, Buffy, Hannah, and LFW | Pairwise clustering F-score and the introduced unified pairwise precision/recall/F-measure for end-to-end detection plus grouping |
| [A Prior-Less Method for Multi-Face Tracking in Unconstrained Videos](https://openaccess.thecvf.com/content_cvpr_2018/html/Lin_A_Prior-Less_Method_CVPR_2018_paper.html) | CVPR 2018 | Eight-video music set and introduced four-video body-worn-camera set | Unknown-K WCP/cluster count plus CLEAR MOT tracking metrics |
| [Self-Supervised Learning of Face Representations for Video Face Clustering](https://www.cs.toronto.edu/~makarand/papers/FG2019_FClst.pdf) | IEEE FG 2019 | BBT-0101, Buffy-0502, and ACCIO | Frame-level clustering accuracy on BBT/Buffy and precision/recall/F-score on ACCIO |
| [MovieNet: A Holistic Dataset for Movie Understanding](https://arxiv.org/abs/2007.10937) | ECCV 2020 | Introduces MovieNet and its genre, cinematic-style, character, scene, and segment-retrieval tasks | Cross-component source; no single MovieNet score represents the VidXP system |
| [MovieGraphs: Towards Understanding Human-Centric Situations from Videos](https://openaccess.thecvf.com/content_cvpr_2018/papers/Vicol_MovieGraphs_Towards_Understanding_CVPR_2018_paper.pdf) | CVPR 2018 | Introduces MovieGraphs; evaluates graph-to-description/dialog/video retrieval, interaction ordering, reason prediction, face clustering, and person ID | Also supplies BCL's supervised train/validation movies; raw-movie access still gates a VidXP run |
| [Dynamic Character Graph via Online Face Clustering for Movie Analysis](https://arxiv.org/abs/2007.14913) | Multimedia Tools and Applications 2020 | BF2006, NH2016, and six full movies for act-boundary and major-character evaluation | Chronological clustering context; the six-movie downstream labels are manually sourced rather than a portable benchmark package |

## Evaluation-method papers and protocols

These sources matter because metric names alone can hide incompatible evaluation
units.

- The [IJB-B paper](https://openaccess.thecvf.com/content_cvpr_2017_workshops/w6/html/Whitelam_IARPA_Janus_Benchmark-B_CVPR_2017_paper.html)
  and [NIST protocol](https://www.nist.gov/system/files/documents/2021/06/07/ijbb_challenge_documentation_readme.pdf)
  define B-cubed precision, recall, and F-score for face clustering. Distribution
  ended in 2023, so this is protocol context unless the team already has lawful
  access.
- BCL reports WCP, NMI, and predicted cluster counts. VPCD adds character
  precision/recall and separates automatic-termination from oracle-cluster
  protocols. Neither should be mixed with IJB-B B-cubed results as if they were
  the same protocol.
- TVR, QVHighlights, and Charades-STA use non-zero temporal intervals and tIoU.
  VidXP's current point timestamp cannot be passed through those evaluators
  unchanged, but a deterministic interval adapter is sufficient for an
  evaluator-valid baseline.
- DiDeMo's mean IoU is computed over its constrained candidate grid and multiple
  human annotations; it is not interchangeable with unrestricted interval mAP.

## Review labels

When notes are added for the paper team, use one of these labels:

- **Benchmark-defining:** introduces a dataset or official evaluation.
- **Comparable zero-shot baseline:** can be run under the same frozen protocol
  without task-specific training.
- **Supervised comparator:** useful context but must be separated from zero-shot
  VidXP results.
- **Architecture/context only:** informs design but is not a numerical baseline.
- **Artifact blocked:** relevant paper whose media, code, or evaluator cannot
  currently support reproduction.

## Items requiring a future release or access recheck

- Whether VRAgent's currently absent TVR-1200/MM-MSRVTT annotations, evaluator,
  predictions, or implementation are released later.
- Exact Creative Commons variant for TVR-Ranking.
- Explicit reuse license for QuerYD annotations and narrator audio.
- Current legal path to TVR clips with audio.
- Current raw-video survival rate for LongVALE and MultiVENT 2.0.
- Whether a corrected VPCD release or implementation has appeared outside the
  official project and GitHub pages.
- Whether the MovieFaceCluster dataset has moved from its now-broken paper link.
