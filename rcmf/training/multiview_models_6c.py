from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import ast
import copy
import math
import random
import re
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.schemas import DecisionExample
from rcmf.training.interaction_representation_6c import interaction_objective
from rcmf.training.oracle_convergence_5fa import custom_huber
from rcmf.training.state_conditioned_transition_6b import DenseTower


MULTIVIEW_MODEL_VERSION = "decomposed_multiview_interaction_6c_v1"
MODEL_KINDS = (
    "multiview_signed_bilinear",
    "multiview_lowrank_tensor",
    "multiview_pair_mlp",
)
API_CALL_RE = re.compile(r"\bapis\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
CODE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
QUOTED_RE = re.compile(r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", re.DOTALL)
PYTHON_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ViewSetMainEffectHeads(nn.Module):
    def __init__(
        self,
        *,
        state_views: int,
        transition_views: int,
        input_dim: int,
        projection_dim: int,
        hidden_dim: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.state_views = int(state_views)
        self.transition_views = int(transition_views)
        self.state_projections = nn.ModuleList(
            nn.Linear(input_dim, projection_dim) for _ in range(state_views)
        )
        self.transition_projections = nn.ModuleList(
            nn.Linear(input_dim, projection_dim) for _ in range(transition_views)
        )
        self.state_head = DenseTower(
            state_views * projection_dim,
            1,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.transition_head = DenseTower(
            transition_views * projection_dim,
            1,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )

    @staticmethod
    def _project(values: Tensor, projections: nn.ModuleList) -> Tensor:
        return torch.cat(
            [F.gelu(layer(values[:, index])) for index, layer in enumerate(projections)],
            dim=-1,
        )

    def state(self, values: Tensor) -> Tensor:
        return self.state_head(self._project(values, self.state_projections)).squeeze(-1)

    def transition(self, values: Tensor) -> Tensor:
        return self.transition_head(
            self._project(values, self.transition_projections)
        ).squeeze(-1)


def train_view_main_effects(
    *,
    model: ViewSetMainEffectHeads,
    decomposition: Mapping[str, Any],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    epochs: int,
    learning_rate: float,
    weight_decay: float,
    huber_delta: float,
    seed: int,
    device: torch.device,
) -> dict[str, Any]:
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device).train()
    state_ids = sorted(str(value) for value in decomposition["state_effects"])
    transition_ids = sorted(str(value) for value in decomposition["transition_effects"])
    state_x = torch.stack(
        [state_representations[state_position[value]] for value in state_ids]
    ).to(device=device, dtype=torch.float32)
    transition_x = torch.stack(
        [transition_representations[transition_position[value]] for value in transition_ids]
    ).to(device=device, dtype=torch.float32)
    state_y = torch.tensor(
        [float(decomposition["state_effects"][value]) for value in state_ids],
        device=device,
    )
    transition_y = torch.tensor(
        [float(decomposition["transition_effects"][value]) for value in transition_ids],
        device=device,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay)
    )
    history = []
    for epoch in range(1, int(epochs) + 1):
        state_loss = custom_huber(
            model.state(state_x) - state_y, delta=float(huber_delta)
        ).mean()
        transition_loss = custom_huber(
            model.transition(transition_x) - transition_y, delta=float(huber_delta)
        ).mean()
        loss = state_loss + transition_loss
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu()
        )
        optimizer.step()
        if epoch == 1 or epoch % 20 == 0 or epoch == int(epochs):
            history.append(
                {
                    "epoch": epoch,
                    "loss": float(loss.detach().cpu()),
                    "state_huber": float(state_loss.detach().cpu()),
                    "transition_huber": float(transition_loss.detach().cpu()),
                    "gradient_norm": gradient_norm,
                }
            )
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return {
        "epochs": int(epochs),
        "optimizer_updates": int(epochs),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


class MultiViewInteractionPredictor(nn.Module):
    def __init__(
        self,
        kind: str,
        *,
        main_effects: ViewSetMainEffectHeads,
        mu: float,
        state_views: int,
        transition_views: int,
        input_dim: int,
        projection_dim: int,
        interaction_rank: int,
        hidden_dim: int,
        dropout: float,
    ) -> None:
        super().__init__()
        if kind not in MODEL_KINDS:
            raise ValueError(f"Unknown multi-view interaction kind: {kind}")
        self.kind = kind
        self.state_views = int(state_views)
        self.transition_views = int(transition_views)
        self.projection_dim = int(projection_dim)
        self.main_effects = copy.deepcopy(main_effects)
        for parameter in self.main_effects.parameters():
            parameter.requires_grad_(False)
        self.register_buffer("mu", torch.tensor(float(mu), dtype=torch.float32))
        self.state_projection = nn.ModuleList(
            nn.Linear(input_dim, projection_dim, bias=False)
            for _ in range(state_views)
        )
        self.transition_projection = nn.ModuleList(
            nn.Linear(input_dim, projection_dim, bias=False)
            for _ in range(transition_views)
        )
        if kind == "multiview_signed_bilinear":
            self.bilinear = nn.Parameter(
                torch.empty(
                    state_views,
                    transition_views,
                    projection_dim,
                    projection_dim,
                )
            )
            nn.init.normal_(self.bilinear, std=1.0 / math.sqrt(projection_dim))
        elif kind == "multiview_lowrank_tensor":
            self.state_rank = nn.Linear(projection_dim, interaction_rank, bias=False)
            self.transition_rank = nn.Linear(
                projection_dim, interaction_rank, bias=False
            )
            self.tensor_core = nn.Parameter(
                torch.zeros(state_views, transition_views, interaction_rank)
            )
            nn.init.normal_(self.tensor_core, std=1.0 / math.sqrt(interaction_rank))
        else:
            feature_dim = (
                (state_views + transition_views) * projection_dim
                + state_views * transition_views * projection_dim
                + state_views * transition_views
            )
            self.pair_head = DenseTower(
                feature_dim, 1, hidden_dim=hidden_dim, dropout=dropout
            )

    @staticmethod
    def _project(values: Tensor, projections: nn.ModuleList) -> Tensor:
        return torch.stack(
            [layer(values[:, index]) for index, layer in enumerate(projections)],
            dim=1,
        )

    def interaction(self, state: Tensor, transition: Tensor) -> Tensor:
        q = self._project(state, self.state_projection)
        k = self._project(transition, self.transition_projection)
        if self.kind == "multiview_signed_bilinear":
            transformed = torch.einsum("bvi,vwij->bvwj", q, self.bilinear)
            return (
                transformed * k[:, None, :, :]
            ).sum(dim=(1, 2, 3)) / math.sqrt(
                self.projection_dim * self.state_views * self.transition_views
            )
        if self.kind == "multiview_lowrank_tensor":
            q_rank = self.state_rank(q)
            k_rank = self.transition_rank(k)
            return torch.einsum(
                "bvr,vwr,bwr->b", q_rank, self.tensor_core, k_rank
            ) / math.sqrt(self.state_views * self.transition_views * q_rank.shape[-1])
        products = q[:, :, None, :] * k[:, None, :, :]
        cosines = F.cosine_similarity(q[:, :, None, :], k[:, None, :, :], dim=-1)
        features = torch.cat(
            (
                q.flatten(1),
                k.flatten(1),
                products.flatten(1),
                cosines.flatten(1),
            ),
            dim=-1,
        )
        return self.pair_head(features).squeeze(-1)

    def components(self, state: Tensor, transition: Tensor) -> dict[str, Tensor]:
        state_main = self.main_effects.state(state)
        transition_main = self.main_effects.transition(transition)
        interaction = self.interaction(state, transition)
        return {
            "mu": self.mu.expand_as(interaction),
            "state_main": state_main,
            "transition_main": transition_main,
            "interaction": interaction,
            "score": self.mu + state_main + transition_main + interaction,
        }

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        return self.components(state, transition)["score"]


class StructuredInteractionPredictor(nn.Module):
    def __init__(
        self,
        *,
        main_effects: ViewSetMainEffectHeads,
        mu: float,
        feature_dim: int,
        hidden_dim: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.kind = "structured_feature_interaction"
        self.main_effects = copy.deepcopy(main_effects)
        for parameter in self.main_effects.parameters():
            parameter.requires_grad_(False)
        self.register_buffer("mu", torch.tensor(float(mu), dtype=torch.float32))
        self.interaction_head = DenseTower(
            feature_dim, 1, hidden_dim=hidden_dim, dropout=dropout
        )

    def components(
        self, state: Tensor, transition: Tensor, pair_features: Tensor
    ) -> dict[str, Tensor]:
        state_main = self.main_effects.state(state)
        transition_main = self.main_effects.transition(transition)
        interaction = self.interaction_head(pair_features).squeeze(-1)
        return {
            "mu": self.mu.expand_as(interaction),
            "state_main": state_main,
            "transition_main": transition_main,
            "interaction": interaction,
            "score": self.mu + state_main + transition_main + interaction,
        }


def _row_tensors(
    rows: Sequence[Mapping[str, Any]],
    *,
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
) -> tuple[Tensor, Tensor, Tensor]:
    state = torch.stack(
        [state_representations[state_position[str(row["state_example_id"])]] for row in rows]
    ).to(device=device, dtype=torch.float32)
    transition = torch.stack(
        [
            transition_representations[transition_position[str(row["transition_id"])]]
            for row in rows
        ]
    ).to(device=device, dtype=torch.float32)
    utility = torch.tensor(
        [float(row["text_utility"]) for row in rows],
        device=device,
        dtype=torch.float32,
    )
    return state, transition, utility


def exact_training_residuals(
    rows: Sequence[Mapping[str, Any]], decomposition: Mapping[str, Any]
) -> Tensor:
    return torch.tensor(
        [
            float(row["text_utility"])
            - float(decomposition["mu"])
            - float(decomposition["state_effects"][str(row["state_example_id"])])
            - float(decomposition["transition_effects"][str(row["transition_id"])])
            for row in rows
        ],
        dtype=torch.float32,
    )


def _state_groups(rows: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row["state_example_id"])].append(index)
    return [grouped[value] for value in sorted(grouped)]


def train_multiview_interaction(
    *,
    model: MultiViewInteractionPredictor | StructuredInteractionPredictor,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    epochs: int,
    seed: int,
    device: torch.device,
    pair_features: Tensor | None = None,
) -> dict[str, Any]:
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device).train()
    model.main_effects.eval()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable,
        lr=float(settings["learning_rate"]),
        weight_decay=float(settings["weight_decay"]),
    )
    state, transition, utility = _row_tensors(
        rows,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        device=device,
    )
    features = (
        pair_features.to(device=device, dtype=torch.float32)
        if pair_features is not None
        else None
    )
    residual = exact_training_residuals(rows, decomposition).to(device)
    groups = _state_groups(rows)
    history = []
    for epoch in range(1, int(epochs) + 1):
        components = (
            model.components(state, transition, features)
            if isinstance(model, StructuredInteractionPredictor)
            else model.components(state, transition)
        )
        loss, losses = interaction_objective(
            score=components["score"],
            interaction=components["interaction"],
            utility=utility,
            residual_target=residual,
            state_groups=groups,
            residual_huber_delta=float(settings["residual_huber_delta"]),
            utility_huber_delta=float(settings["utility_huber_delta"]),
            teacher_temperature=float(settings["teacher_temperature"]),
            student_temperature=float(settings["student_temperature"]),
            pair_gap_threshold=float(settings["pair_gap_threshold"]),
            pair_gap_clip=float(settings["pair_gap_clip"]),
            loss_weights=settings["losses"],
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(
            torch.nn.utils.clip_grad_norm_(trainable, 1.0).detach().cpu()
        )
        optimizer.step()
        if epoch == 1 or epoch % 10 == 0 or epoch == int(epochs):
            history.append(
                {
                    "epoch": epoch,
                    "total_loss": float(loss.detach().cpu()),
                    **{key: float(value.detach().cpu()) for key, value in losses.items()},
                    "gradient_norm": gradient_norm,
                }
            )
    model.eval()
    return {
        "epochs": int(epochs),
        "optimizer_updates": int(epochs),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _code_features(text: str) -> tuple[set[str], set[str], set[str]]:
    calls: set[str] = set()
    strings: set[str] = set()
    tokens = {value.lower() for value in CODE_TOKEN_RE.findall(text)}
    blocks = PYTHON_BLOCK_RE.findall(text) or [text]
    for block in blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                if name:
                    calls.add(name.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip().lower()
                if value:
                    strings.add(value)
    strings.update(
        match.group("value").strip().lower()
        for match in QUOTED_RE.finditer(text)
        if match.group("value").strip()
    )
    return calls, strings, tokens


def _set_metrics(left: set[str], right: set[str]) -> tuple[float, float, float]:
    shared = len(left.intersection(right))
    union = len(left.union(right))
    return float(bool(shared)), math.log1p(shared), shared / union if union else 0.0


def _step_bucket(step: int, count: int) -> str:
    ratio = (int(step) - 1) / max(int(count) - 1, 1)
    return "early" if ratio < 1 / 3 else "middle" if ratio < 2 / 3 else "late"


class StructuredPairFeatureBuilder:
    ACTION_TYPES = (
        "api_documentation",
        "api_read_or_login",
        "api_mutation",
        "completion",
        "other",
    )
    STEP_BUCKETS = ("early", "middle", "late")

    def __init__(
        self,
        *,
        state_examples: Mapping[str, DecisionExample],
        state_metadata: Mapping[str, Mapping[str, Any]],
        transitions: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.transition: dict[str, dict[str, Any]] = {}
        for state_id, example in state_examples.items():
            calls, strings, tokens = _code_features(example.state_text)
            api_names = {f"{app}.{api}" for app, api in API_CALL_RE.findall(example.state_text)}
            metadata = state_metadata[state_id]
            self.state[state_id] = {
                "apps": set(str(value) for value in metadata.get("apps", [])),
                "apis": api_names,
                "calls": calls,
                "strings": strings,
                "tokens": tokens,
                "step_bucket": _step_bucket(
                    int(metadata["step_id"]), int(metadata["step_count"])
                ),
                "prompt_tokens": int(metadata["prompt_tokens"]),
                "goal_tokens": len(CODE_TOKEN_RE.findall(example.state_text.split("[TRACE SO FAR]", 1)[0])),
                "history_tokens": len(CODE_TOKEN_RE.findall(example.state_text.split("[TRACE SO FAR]", 1)[-1])) if "[TRACE SO FAR]" in example.state_text else 0,
            }
        for transition_id, row in transitions.items():
            calls, strings, tokens = _code_features(str(row["complete_action"]))
            observation_tokens = {
                value.lower()
                for value in CODE_TOKEN_RE.findall(
                    str(row["complete_post_action_observation"])
                )
            }
            self.transition[transition_id] = {
                "apps": set(str(value) for value in row.get("apps", [])),
                "apis": set(str(value) for value in row.get("api_names", [])),
                "calls": calls,
                "strings": strings,
                "tokens": tokens,
                "observation_tokens": observation_tokens,
                "action_type": str(row.get("action_type", "other")),
                "step_bucket": _step_bucket(
                    int(row["step_index"]), int(row["step_count"])
                ),
                "source_tokens": int(row["canonical_pre_action_state_tokens"]),
                "action_tokens": int(row["complete_action_tokens"]),
                "observation_length": int(row["complete_post_action_observation_tokens"]),
                "goal_tokens": int(row["source_task_goal_tokens"]),
            }
        self.feature_names = self._feature_names()

    def _feature_names(self) -> list[str]:
        names = []
        for family in ("apps", "apis", "calls", "strings", "code_tokens"):
            names.extend((f"{family}_any", f"{family}_shared_log1p", f"{family}_jaccard"))
        names.extend(("state_tokens_vs_observation_any", "state_tokens_vs_observation_shared_log1p", "state_tokens_vs_observation_jaccard"))
        names.extend(f"action_type={value}" for value in self.ACTION_TYPES)
        names.extend(f"state_step={value}" for value in self.STEP_BUCKETS)
        names.extend(f"transition_step={value}" for value in self.STEP_BUCKETS)
        names.extend(
            (
                "log_state_prompt_tokens",
                "log_state_goal_tokens",
                "log_state_history_tokens",
                "log_transition_source_tokens",
                "log_transition_goal_tokens",
                "log_transition_action_tokens",
                "log_transition_observation_tokens",
            )
        )
        return names

    def vector(self, state_id: str, transition_id: str) -> Tensor:
        state = self.state[str(state_id)]
        transition = self.transition[str(transition_id)]
        values: list[float] = []
        for left_name, right_name in (
            ("apps", "apps"),
            ("apis", "apis"),
            ("calls", "calls"),
            ("strings", "strings"),
            ("tokens", "tokens"),
        ):
            values.extend(_set_metrics(state[left_name], transition[right_name]))
        values.extend(_set_metrics(state["tokens"], transition["observation_tokens"]))
        action_type = transition["action_type"]
        values.extend(float(action_type == value) for value in self.ACTION_TYPES)
        values.extend(float(state["step_bucket"] == value) for value in self.STEP_BUCKETS)
        values.extend(float(transition["step_bucket"] == value) for value in self.STEP_BUCKETS)
        values.extend(
            math.log1p(float(value))
            for value in (
                state["prompt_tokens"],
                state["goal_tokens"],
                state["history_tokens"],
                transition["source_tokens"],
                transition["goal_tokens"],
                transition["action_tokens"],
                transition["observation_length"],
            )
        )
        if len(values) != len(self.feature_names):
            raise RuntimeError("Structured feature dimension differs")
        return torch.tensor(values, dtype=torch.float32)

    def rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        state_map: Mapping[str, str] | None = None,
        transition_map: Mapping[str, str] | None = None,
    ) -> Tensor:
        return torch.stack(
            [
                self.vector(
                    (state_map or {}).get(
                        str(row["state_example_id"]), str(row["state_example_id"])
                    ),
                    (transition_map or {}).get(
                        str(row["transition_id"]), str(row["transition_id"])
                    ),
                )
                for row in rows
            ]
        )
