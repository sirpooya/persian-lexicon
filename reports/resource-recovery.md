# Resource recovery, 2026-10-27

| Resource | Result |
|---|---|
| Zaya | Partially recovered through a pinned G2P derivative: 53,627 word/pronunciation rows, 52,963 distinct normalized headwords, 5,800 new entries in the combined lexicon. The full original download did not return a file. |
| MULTEXT-East Persian | Official file and download-all links returned web pages; the inspected GitHub mirror excludes Persian. |
| Vafa | Original project domains failed DNS resolution; no standalone dictionary download verified. |
| FarsVajeh | No public release verified; owner lab website timed out. |
| ELRA-L0087 | Catalogue offers separate paid acquisition; no anonymous download obtained. |

Zaya's pronunciation alternatives are not frequencies or additional evidence. The filtered
G2P copy is not presented as the complete original Zaya release. Its source is pinned in
`config/sources.json`, and the build preserves its provenance as `zaya_g2p`.
The new source contributes 5,800 otherwise absent normalized entries. Another 14,559
existing single-token entries now meet the two-family core rule; this is added source
support, not manual validation. The resulting build has 2,858,612 entries, with 583,539
dictionary-supported single tokens and 60,586 core tokens.

Detailed attempts and evidence URLs are recorded in [resource-recovery.json](resource-recovery.json).
Sources: [Zaya derivative](https://github.com/mohamad-hasan-sohan-ajini/G2P/tree/2b51e0676983a131bd7d5aaf235424ab15469e6e/resources/FA),
[MULTEXT repository](https://www.clarin.si/repository/xmlui/handle/11356/1042),
[ELRA catalogue](https://catalog.elra.info/en-us/repository/browse/ELRA-L0087/).
