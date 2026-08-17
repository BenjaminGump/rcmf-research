# EXP-025B Live Replay/Qwen Bridge Report

The AppWorld 0.1.0 worker replays history, retains the world and Python
namespace, emits actual replay observations for prompt construction, accepts
one generated action, executes it in that same world/namespace, and writes one
atomic result. Every condition receives a fresh worker.

The smoke passed `4/4` conditions, preserved a `passwords` replay variable,
verified live JWT/prompt/world continuity, and proved an interrupted condition
leaves no atomic result before successful resume. The formal run passed
`323/323` history, same-world, and same-namespace checks with zero bridge
exceptions or duplicate results.
