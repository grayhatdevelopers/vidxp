# Research adoption record

Collection index: [Benchmarking research](README.md)

Status: Current source of truth

Last verified: 2026-09-03

This page records which published ideas are in VidXP, where they are used, and
where VidXP deviates. The [paper inventory](research_papers.md) and
[validation ledger](paper_validation.md) cover reviewed work that is not
adopted.

## Status meanings

- **Adopted**: used in the current product path.
- **Control**: current behavior retained for comparison, without a claim that
  it is the final design.
- **Experiment**: benchmark-only code, not product behavior.
- **Candidate**: reviewed but neither adopted nor implemented.

A similar-looking implementation is not paper-derived after the fact. Every
paper-derived change must name the exact source and method, document deviations,
and record the evidence used to accept it. Original VidXP engineering must be
labeled as such.

## Product adoptions

| Source | Adopted part and location | Reason | VidXP deviation or limit |
| --- | --- | --- | --- |
| Li et al., [FineLAP](https://aclanthology.org/2026.acl-long.473/), ACL 2026, Sections 3.2–3.3 | Released global and local audio representations in `src/vidxp/capabilities/sound/` | Supplies environmental-sound retrieval and timestamped activation features | Standard search uses global clips to select regions, then ranks only local activations inside them. The two-stage orchestration, ten-second windows, 0.16-second records, context metadata, and fallback are VidXP choices. |
| Cormack, Clarke, and Buettcher, [Reciprocal Rank Fusion](https://doi.org/10.1145/1571941.1572114), SIGIR 2009 | Rank-only formula with `k = 60` in `src/vidxp/search_fusion.py` | Combines modality rankings without treating their raw distances as one scale | Connected temporal grouping, one best rank per modality, and interval union are VidXP controls, not parts of the paper. |
| Zhao et al., [VideoPrism](https://arxiv.org/abs/2402.13217), ICML 2024 | Released video encoder in `src/vidxp/capabilities/action/` | Supplies motion-aware clip embeddings | VidXP's non-overlapping 16-frame records are not a VideoPrism boundary method. |
| Tschannen et al., [SigLIP 2](https://arxiv.org/abs/2502.14786), 2025 | Released image-text encoder in `src/vidxp/capabilities/scene/` | Supplies visual-semantic frame retrieval | VidXP samples at 1 fps. These records are sampled frames, not detected semantic scenes. |
| Radford et al., [Whisper](https://arxiv.org/abs/2212.04356), ICML 2023, and Zhang et al., [Qwen3 Embedding](https://arxiv.org/abs/2506.05176), 2025 | Speech recognition and text embeddings in `src/vidxp/capabilities/speech/` | Produces timestamped, searchable transcript evidence | `faster-whisper` is the runtime implementation. Segmentation, storage, and retrieval are VidXP choices. |

Existing sound indexes do not need rebuilding for the FineLAP search correction;
their representation metadata already separates global windows from dense
activations.

## Original product controls

| Behavior | Exact status |
| --- | --- |
| Fixed VideoPrism records | Sixteen frames sampled at 2 fps form a record of about eight seconds. No paper was adopted to select this temporal unit. |
| One-second SigLIP 2 records | They provide dense visual evidence, not shot or scene boundaries. |
| FineLAP two-stage search | Global records choose candidate regions. Local records are reranked inside those regions and supply the returned timestamps. Their raw distances are never compared across representations. This orchestration is original VidXP engineering. |
| Connected-interval grouping | Every overlapping hit, including transitive overlaps, enters one component. This is VidXP logic. |
| Component interval union | A component starts at its earliest hit and ends at its latest. It is a coarse evidence envelope and can be widened by one record. |
| Equal `top_k` per modality | Each modality receives the requested retrieval depth. There is no adopted candidate-allocation method. |
| Optional query model | A language model may plan searches or summarize citable evidence. Model size and reasoning are deployment choices, not research adoptions. |

The reverted `4x` over-fetch and anchor-preserving union rule is not adopted. Its
multiplier was selected after one development example and has no general claim.

## Benchmark-only experiments

| ID | Source and scope | Recorded result | Decision |
| --- | --- | --- | --- |
| `p2s_asg_vidxp_v1` | Point-to-Span v1, Section 3.1 only; VidXP score curves and early NMS replace the unreproduced full pipeline | Development IoU changed from `0.7493` to `0.7976`; only sound produced a span, below the direct-inspection agent's `0.8824` | Concluded diagnostic; not adopted |
| `videoprism_overlap_control_v1` | CTAP/Barrios et al. motivate overlapping windows; VidXP tested four-second windows at a two-second stride | Fusion still returned about eight seconds; IoU fell to `0.7477` and action records grew from 10 to 38 | Concluded control; not adopted |
| `diwan_shotdetect_siglip2_v1` | Diwan et al. ShotDetect proposals, scored with existing SigLIP 2 records; VidXP added proposal-level RRF | Development IoU reached `0.8902`; on six scene-comparable held-out tasks RRF reduced mean IoU from `0.2841` to `0.1175` | Proposal-level RRF rejected; code retained as a control |
| `manual_modality_query_ceiling_v1` | Luo et al. and TFVTG motivate decomposition, but manual modality wording is a VidXP ceiling rather than either published method | Top-three target coverage changed from 7/16 to 8/16; nine ranks improved and two worsened | Mandatory rewriting rejected |
| `finelap_separate_streams_v1` | FineLAP Sections 3.2–3.3; global windows and dense activations queried separately | Top-three target coverage changed from 0/4 mixed to 3/4 across separate lists | Supports the product rule not to cross-rank the raw outputs; no local-activation product surface selected |

The experiment code lives in `src/vidxp/benchmarks/` and
`benchmarks/codex-mcp/scripts/`. Frozen settings and task data remain beside the
scripts. These controls may be reproduced, but they are not a queue of product
changes.

## Confirmed conclusions

- The saved development run found the correct opening region. Its
  `0–8.0075`-second result was wider than the `0–6` reference because the
  eight-second action record set the component endpoint.
- FineLAP's global and local records cannot be treated as one raw-distance
  ranking. Standard sound search now uses global clips for candidate selection
  and local activations for the final sound hits.
- RRF is useful as a transparent ranking control, but the current temporal
  grouping and union do not provide exact boundaries.
- No evidence from these controls selects AM-DETR, UMT, UniVTG, or another
  model as the next product implementation.
- The next approved agent comparison should test whether VidXP supplies enough
  evidence for a similarly grounded answer with fewer tokens, less time, or
  fewer media-inspection calls. IoU remains one diagnostic within that result.

## Required record for future adoption

For every paper-derived product change, record:

1. the exact paper, version, venue, and artifact revision;
2. the adopted method component and product code location;
3. every deviation from the published method;
4. quality and resource evidence supporting the decision; and
5. rejected alternatives and why they lost.

For original VidXP engineering, state that it is original and record the same
decision evidence. Do not attach a paper citation retroactively.
