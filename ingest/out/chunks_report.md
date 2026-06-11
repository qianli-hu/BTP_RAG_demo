# Chunking report

Total chunks: **1216** across 3 docs (target 300 tok, max 512, min 25, overlap 1 sent)

| doc | chunks | prose | table | avg tok | max tok | pages |
|---|--:|--:|--:|--:|--:|--:|
| sap-ai-core | 406 | 362 | 44 | 218 | 2417 | 190 |
| sap-ai-launchpad | 618 | 560 | 58 | 244 | 2417 | 374 |
| sap-hana-vector | 192 | 169 | 23 | 161 | 547 | 51 |

Token histogram (100-tok buckets):
    0-99 : ################ 329
  100-199: ############# 273
  200-299: ################### 395
  300-399: ####### 153
  400-499:  15
  500-599: ## 51

---
## Samples (1 per doc)

**sap-ai-core** · `sap-ai-core#b67d3db91030#00` · 283 tok · page 5 · [src](https://help.sap.com/docs/sap-ai-core)
`What Is SAP AI Core?`

> Learn more about the SAP AI Core service on SAP Business Technology Platform (SAP BTP). Build a platform for your artificial intelligence solutions. SAP AI Core is a service within the SAP Business Technology Platform. It's designed to manage the execution and operations of AI assets in a standardized, scalable, and hyperscaler-agnostic manner. It seamlessly integrates with SAP solutions, allowing any AI function to be easily implemented using open-source frameworks. SAP AI Core supports full li…

**sap-ai-launchpad** · `sap-ai-launchpad#115de48f3f26#00` · 296 tok · page 4 · [src](https://help.sap.com/docs/ai-launchpad)
`What Is SAP AI Launchpad?`

> SAP AI Launchpad is a multitenant software as a service (SaaS) application on SAP Business Technology Platform (SAP BTP). Customers and partners can use SAP AI Launchpad to manage AI use cases (scenarios) across multiple instances of AI runtimes (such as SAP AI Core). SAP AI Launchpad also provides generative AI capabilities via the Generative AI Hub. Throughout this document, SAP AI Core is used as an example of an AI runtime. AI runtimes are not included in your SAP AI Launchpad subscription. …

**sap-hana-vector** · `sap-hana-vector#1a201a9b357f#00` · 282 tok · page None · [src](https://help.sap.com/docs/hana-cloud-database/sap-hana-cloud-sap-hana-database-vector-engine-guide/sap-hana-cloud-sap-hana-database-vector-engine-guide)
`SAP HANA Cloud, SAP HANA Database Vector Engine Guide`

> This guide provides information about the SAP HANA Cloud vector engine. This guide is organized as follows: Introduction The SAP HANA Cloud vector engine offers multiple use cases in AI scenarios. Introduction The SAP HANA Cloud vector engine offers multiple use cases in AI scenarios. Vectors, Vector Embeddings, and Similarity Measures Vectors, vector embeddings, and similarity measures are used to evaluate the similarity of two objects. Vectors, Vector Embeddings, and Similarity Measures Vector…
