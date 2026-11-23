"""v1.17 candidate: LLM helpers kept OUT of the trading path (policy editor, buyer message parse assist).
Nothing under hooks/ or the v1.16.1 deploy files imports this package (tests check it). No module here opens a
network connection: every LLM call goes through an injected client object (llm.client.LLMClient); tests use only
the mock clients in llm.client. A real client is not part of this package and needs separate approval."""
