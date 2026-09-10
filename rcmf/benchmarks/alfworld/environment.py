from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rcmf.pipeline.portable_v2.schemas import TaskRecord


ENVIRONMENT_VERSION = "alfworld-aaba687-textworld-1.7.0"


def create_official_text_environment(
    game_path: str | Path,
    *,
    with_expert: bool,
    expert_type: str = "planner",
) -> Any:
    """Create ALFWorld's wrapped TextWorld deployment interface."""

    import textworld
    from alfworld.agents.environment.alfred_tw_env import (
        AlfredDemangler,
        AlfredExpert,
        AlfredInfos,
    )

    request_infos = textworld.EnvInfos(
        won=True,
        admissible_commands=True,
        extras=["gamefile"],
    )
    wrappers: list[Any] = [AlfredDemangler(shuffle=False), AlfredInfos]
    if with_expert:
        wrappers.append(AlfredExpert(expert_type=expert_type))
        request_infos.extras.append("expert_plan")
    return textworld.start(
        str(Path(game_path).resolve(strict=True)),
        request_infos,
        wrappers=wrappers,
    )


class ALFWorldTextRuntime:
    """One exact game reset and stepped through the official text interface."""

    def __init__(
        self,
        task: TaskRecord,
        *,
        data_root: str | Path,
        with_expert: bool = False,
    ) -> None:
        self.task = task
        self.data_root = Path(data_root).resolve(strict=True)
        self.game_path = (self.data_root / str(task.metadata["game_path"])).resolve(strict=True)
        try:
            self.game_path.relative_to(self.data_root)
        except ValueError as exc:
            raise ValueError("ALFWorld game path escaped the sealed data root") from exc
        self.environment = create_official_text_environment(
            self.game_path,
            with_expert=with_expert,
        )
        self.with_expert = bool(with_expert)
        self.state = self.environment.reset()
        self.initial_observation = str(self.state["feedback"])
        self.actions: list[str] = []
        self.observations: list[str] = []
        self.rewards: list[float] = []
        self.done = False
        self.error: Mapping[str, Any] | None = None

    @property
    def observation(self) -> str:
        return self.initial_observation if not self.observations else self.observations[-1]

    def expert_command(self) -> str:
        if not self.with_expert:
            raise RuntimeError("runtime was not created with the official planner")
        plan = self.state.get("extra.expert_plan")
        if not isinstance(plan, (list, tuple)) or not plan:
            raise RuntimeError("official ALFWorld planner returned no command")
        command = plan[0]
        if not isinstance(command, str) or not command.strip():
            raise RuntimeError("official ALFWorld planner returned an invalid command")
        admissible = self.state.get("admissible_commands") or ()
        if admissible and command not in admissible:
            raise RuntimeError("official ALFWorld planner command is not admissible")
        return command

    def step(self, action: str) -> dict[str, Any]:
        if self.done:
            raise RuntimeError("cannot step a terminal ALFWorld runtime")
        command = str(action).strip()
        if not command:
            raise ValueError("ALFWorld action must be non-empty")
        self.state, reward, done = self.environment.step(command)
        observation = str(self.state["feedback"])
        self.actions.append(command)
        self.observations.append(observation)
        self.rewards.append(float(reward))
        self.done = bool(done)
        return {
            "action": command,
            "observation": observation,
            "raw_reward": float(reward),
            "done": self.done,
            "official_won": bool(self.state.get("won", False)),
            "step": len(self.actions) - 1,
        }

    def close(self) -> None:
        self.environment.close()


def validate_text_action(action: str) -> dict[str, Any]:
    command = str(action).strip()
    return {
        "valid": bool(command),
        "normalized_action": command,
        "action_semantics": "alfworld_text_command",
    }
