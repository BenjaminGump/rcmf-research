import re
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from jinja2 import Template
from model import MODEL
from prompt import AGENT_SYSTEM_PROMPT_TEMPLATES, AGENT_QUERY_PROMPT_TEMPLATES
import ctypes
kernel32 = ctypes.windll.kernel32
kernel32.GetTickCount64.restype = ctypes.c_uint64
def get_real_time() -> float:
    """返回真实的绝对时间（秒），用于精确的耗时统计"""
    return kernel32.GetTickCount64() / 1000.0


class BaseAgent:
    def __init__(self, dataset_name: str, max_context: int = 40, max_steps: int = 50):
        self.dataset_name = dataset_name
        self.max_context = max_context
        self.max_steps = max_steps

    def execute(self, **kwargs):
        raise NotImplementedError("Subclasses must implement this method")


class AppWorldAgent(BaseAgent):
    def __init__(self, experiment_name: str, root: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.experiment_name = experiment_name
        self.root = root

    def _accumulate_usage(self, usage1: Dict[str, int], usage2: Dict[str, int]) -> Dict[str, int]:
        """
        累加两个 usage 字典中的 token 计数
        Args:
            usage1: 第一个 usage 字典，包含 "prompt_tokens", "completion_tokens", "total_tokens"
            usage2: 第二个 usage 字典，包含 "prompt_tokens", "completion_tokens", "total_tokens"
        Returns:
            一个新的字典，包含累加后的 "prompt_tokens", "completion_tokens", "total_tokens"
        """
        accumulated = {
            "prompt_tokens": usage1.get("prompt_tokens", 0) + usage2.get("prompt_tokens", 0),
            "completion_tokens": usage1.get("completion_tokens", 0) + usage2.get("completion_tokens", 0),
            "total_tokens": usage1.get("total_tokens", 0) + usage2.get("total_tokens", 0)
        }
        return accumulated

    def _system_prompt_messages(self, system_prompt: str) -> list[dict]:
        """
        将初始的文段prompt转化成对话格式的messages
        Args:
            system_prompt: prompt.py中的初始system prompt文段
        """
        messages: list[dict] = []
        last_start = 0
        for match in re.finditer("(USER|ASSISTANT|SYSTEM):\n", system_prompt):
            last_end = match.span()[0]
            if len(messages) == 0:
                if last_end != 0:
                    raise ValueError(
                        f"Start of the prompt has no assigned role: {system_prompt[:last_end]}"
                    )
            else:
                messages[-1]["content"] = system_prompt[last_start:last_end].rstrip()
            mesg_type = match.group(1).lower()
            messages.append({"role": mesg_type, "content": None})
            last_start = match.span()[1]
        messages[-1]["content"] = system_prompt[last_start:]
        return messages

    def _extract_code_and_fix_content(self, text: str) -> tuple[str, str]:
        """
        从模型回复中提取可执行的code
        Args:
            text: 模型回复的文本内容
        """
        if text is None:
            return "", ""
        partial_code_regex = r".*```python\n(.*)"
        full_code_regex = r"```python\n(.*?)```"
        ignore_multiple_calls = True
        original_text = text
        output_code = ""
        match_end = 0
        # Handle multiple calls
        for re_match in re.finditer(full_code_regex, original_text, flags=re.DOTALL):
            code = re_match.group(1).strip()
            if ignore_multiple_calls:
                text = original_text[: re_match.end()]
                return code, text
            output_code += code + "\n"
            match_end = re_match.end()
        # Check for partial code match at end (no terminating ```)  following the last match
        partial_match = re.match(partial_code_regex, original_text[match_end:], flags=re.DOTALL)
        if partial_match:
            output_code += partial_match.group(1).strip()
            # Terminated due to stop condition; add stop condition to output
            if not text.endswith("\n"):
                text = text + "\n"
            text = text + "```"
        if len(output_code) == 0:
            return "", text
        else:
            return output_code, text

    def _evaluate(self, experiment_name: str, task_id: str, root: str = ".") -> str:
        """
        在AppWorld环境中执行评估命令并返回任务是否成功
        Args:
            experiment_name: 实验名称(如'minimal_react_agent')
            task_id: 具体任务ID(如'fac291d_1')
            root: AppWorld根目录，默认为当前目录'.'
        """
        # 1.构建命令
        # 直接使用'appworld'命令，因为环境已经配置好且取消了安全限制
        cmd = ["appworld", "evaluate", experiment_name]
        # 确定文件名规则和命令参数
        cmd.extend(["--task-id", task_id])
        json_file_name = f"on_only_{task_id}.json"
        if root != ".":
            cmd.extend(["--root", root])

        # 2.执行命令
        try:
            print(f"Running command: {' '.join(cmd)}")
            # check=True会在命令返回非零状态码时抛出异常
            subprocess.run(cmd, check=True, cwd=root, shell=True)
        except subprocess.CalledProcessError as e:
            print(f"Error during appworld evaluation: {e}")
            return False

        # 3.构造结果JSON的完整路径
        # 路径结构:{root}/experiments/outputs/{experiment_name}/evaluations/{json_file_name}
        result_path = Path(root) / "experiments" / "outputs" / experiment_name / "evaluations" / json_file_name

        # 4.读取JSON并提取success字段
        if not result_path.exists():
            print(f"Result file not found: {result_path}")
            return False

        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 5. 获取task_goal_completion值，如果是单任务的评估结果，一般会是100.0或0.0，表示成功或失败；如果是多任务评估，则是一个0到100的正确率，换言之evaluate返回的就是本次测试的正确率
        acc = data.get("aggregate", {}).get("task_goal_completion", 0.0)
        return float(acc) == 100.0

    def execute(self, **kwargs):
        t0 = get_real_time()
        task_id = kwargs.get("task_id")
        if not task_id:
            raise ValueError("Missing required argument: task_id")

        accumulated_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        from appworld import AppWorld
        t1 = get_real_time()
        print(f"AppWorld imported, time taken: {t1 - t0:.2f}s")
        with AppWorld(task_id=task_id, experiment_name=self.experiment_name) as world:
            trace = []
            conversation_history = []

            current_query = world.task.instruction

            system_template = AGENT_SYSTEM_PROMPT_TEMPLATES.get(self.dataset_name)
            system_messages = self._system_prompt_messages(system_template)

            # 初始用户查询
            query_template = AGENT_QUERY_PROMPT_TEMPLATES.get(self.dataset_name)
            query_dictionary = {"world": world}
            query_prompt = Template(query_template.lstrip()).render(query_dictionary)
            conversation_history.append({"role": "user", "content": query_prompt})
            trace.append(f"USER:\n{query_prompt}")

            # ReAct循环
            t2 = get_real_time()
            print(f"Initial setup done, time taken: {t2 - t1:.2f}s")
            for step in range(self.max_steps):
                t3 = get_real_time()
                messages = system_messages.copy()
                # 添加对话历史(保留最近self.max_context轮)
                if len(conversation_history) > self.max_context:
                    recent_history = [conversation_history[0]] + conversation_history[-(self.max_context-1):]
                else:
                    recent_history = conversation_history
                # 将system prompt和recent_history拼接起来作为LLM的输入
                messages.extend([{"role": msg["role"], "content": msg["content"]} for msg in recent_history])
                t4 = get_real_time()
                print(f"Constructed messages for LLM, step {step + 1}: {t4 - t3:.2f}s, total time since start: {t4 - t2:.2f}s")
                # 调用LLM
                response, usage = MODEL.forward(messages=messages, temperature=0.3)
                accumulated_usage = self._accumulate_usage(accumulated_usage, usage)
                t5 = get_real_time()
                time_cost = t5 - t4
                print(f"Received response from LLM, step {step + 1}: {time_cost:.2f}s, total time since start: {t5 - t2:.2f}s")

                code, text = self._extract_code_and_fix_content(response)
                t6 = get_real_time()
                print(f"Extracted code from LLM response, step {step + 1}: {t6 - t5:.2f}s, total time since start: {t6 - t2:.2f}s")
                trace.append(f"ASSISTANT:\n{text}")
                conversation_history.append({"role": "assistant", "content": text})

                # 执行工具
                observation = world.execute(code)
                t7 = get_real_time()
                print(f"Executed code in AppWorld, step {step + 1}: {t7 - t6:.2f}s, total time since start: {t7 - t2:.2f}s")

                trace.append(f"USER:\nOutput:\n```\n{observation}\n```")
                conversation_history.append({"role": "user", "content": f"Output:\n```\n{observation}\n```"})

                if world.task_completed():
                    break

            if world.task_completed():
                is_correct = self._evaluate(experiment_name=self.experiment_name, task_id=task_id, root=self.root)
            else:
                is_correct = False

            t9 = get_real_time()
            print(f"Evaluation completed, time taken: {t9 - t7:.2f}s, total time since start: {t9 - t2:.2f}s")

            log_dir = Path(self.root) / "experiments" / "outputs" / self.experiment_name / "evaluations"
            log_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
            log_path = log_dir / f"{task_id}.json"

            log_data = {
                "task_id": task_id,
                "is_correct": is_correct,
                "usage": accumulated_usage,
                "trace": trace,
            }

            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_data, f, indent=4, ensure_ascii=False)
            t11 = get_real_time()
            print(f"Logged results to {log_path}, time taken: {t11 - t9:.2f}s, total time since start: {t11 - t2:.2f}s")

            return is_correct, trace
