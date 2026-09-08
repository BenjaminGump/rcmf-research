# Prompt Sources

## Selected Engineering Defaults

| Dataset | Profile | Primary source | Pinned commit | Local asset | SHA256 | License | Status |
|---|---|---|---|---|---|---|---|
| ALFWorld | `react_task_type_two_demo_v1` | `ysymyth/ReAct`, `prompts/alfworld_3prompts.json` + notebook assembly | `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9` | `assets/prompts/alfworld/react_task_type_two_demo_v1/alfworld_3prompts.json` | `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` | MIT | default candidate, no scientific run |
| WebShop | `react_official_one_demo_v1` | `ysymyth/ReAct`, `WebShop.ipynb` `prompt1` | `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9` | `assets/prompts/webshop/react_official_one_demo_v1/prompt1.txt` | `58e4164eb648db4f8f437ee8a7b3ce064d3661d929e4f0fe71155ea6b062c56a` | MIT | default candidate, no scientific run |

ALFWorld's upstream blob is
`0e7c204818e9aa3128d266da5512d1ec3d9ea13e`. The exact JSON file SHA is
`a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a`.
The assembly notebook blob is
`645a82bd748d1bde50c48bdf67397c905b9a7e6f`, with file SHA256
`a7fcbe884dda2c838b55490f6812c28be66c6bb28d26061e6987eba7246178e1`.
The notebook assembly selects task-family examples `_1` then
`_0` using `put`, `clean`, `heat`, `cool`, `examine`, or `puttwo` and adds its
exact header/trailer. That mapping lives only in the ALFWorld renderer.

WebShop's notebook blob is
`67cb9f878de242951a7804a31bb5da4264111a29`; notebook bytes SHA256 are
`86c7bb6c8a3a2de6aa0fde45da280c22a7332864897bd5e686a4634df0e36b0f`.
`prompt1` was extracted programmatically as a Python literal, not transcribed.
Its Webshop header, instruction/Search markers, Action/Observation form, and
`search[...]`, `think[...]`, `click[...]` grammar are unchanged.

The exact machine manifests are under `assets/prompts/source_manifests/`.
Renderers return a declared single `user` message and add no implicit system
prompt. The future dataset task must bind the frozen Qwen tokenizer/chat
template and record runtime-rendered hashes/token counts before science.

## Secondary References

ExpeL is pinned at `e41ec9a24823e7b560c561ab191441b56d9bcefc`
(Apache-2.0): `prompts/alfworld.py` SHA256
`27dfa16517409a10ec34e1423929f030ce579c78cc177e8fff9709e5e0ae77ec`
and `prompts/webshop.py` SHA256
`60faa7442a6b5b1941bef0e37dc5e6d6e218f883dd61868e12e7dab909efe0a5`.
It is
`REFERENCE_ONLY`. Reflection, insight, retrieval, and experiential-memory
instructions are method-confounded and excluded from default RCMF prompts.

Reflexion is pinned at `218cf0ef1df84b05ce379dd4a8e47f17766733a0`
(MIT). Its ALFWorld prompt blob matches the ReAct asset; this is corroborating
reuse evidence, not a second vendored copy.

Upstream URLs and full hashes are in the source manifests. No prompt has been
scientifically selected on ALFWorld or WebShop outcomes.
