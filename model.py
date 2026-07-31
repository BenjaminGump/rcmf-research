"""
DeepSeek API 调用类
支持 DashScope 和 OpenRouter 双引擎切换
"""
from typing import List, Dict, Optional, Tuple, Union, Any
import os
from dotenv import load_dotenv
import time

load_dotenv(override=True)

class MODEL:
    """DeepSeek API 调用类（自动切换 DashScope 和 OpenRouter）"""
    
    @staticmethod
    def _get_provider(api_key: Optional[str] = None) -> str:
        """判断使用哪个服务商"""
        # 1. 如果传入了 key，尝试判断 key 的来源（简单判断，或默认优先逻辑）
        # 2. 检查环境变量
        if api_key:
            return "dashscope" if "sk-" in api_key and len(api_key) < 50 else "openrouter"

        if os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if os.environ.get("DASHSCOPE_API_KEY"):
            return "dashscope"

        raise ValueError("请设置 DASHSCOPE_API_KEY 或 OPENROUTER_API_KEY 环境变量")

    @staticmethod
    def _build_messages(query, messages, sys_prompt):
        if messages is not None:
            final_messages = messages.copy()
            has_system = any(msg.get("role") == "system" for msg in final_messages)
            if sys_prompt and not has_system:
                final_messages.insert(0, {"role": "system", "content": sys_prompt})
            return final_messages
        
        final_messages = []
        if sys_prompt:
            final_messages.append({"role": "system", "content": sys_prompt})
        if query:
            final_messages.append({"role": "user", "content": query})
        else:
            raise ValueError("必须提供 query 或 messages 参数")
        return final_messages

    @staticmethod
    def _extract_usage_dashscope(response) -> dict:
        """统一 Dashscope 的 token 统计格式"""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if hasattr(response, 'usage'):
            # Dashscope 通常使用 input_tokens 和 output_tokens
            # 兼容对象访问和字典访问
            r_usage = response.usage
            if isinstance(r_usage, dict):
                usage["prompt_tokens"] = r_usage.get("input_tokens", 0) or r_usage.get("prompt_tokens", 0)
                usage["completion_tokens"] = r_usage.get("output_tokens", 0) or r_usage.get("completion_tokens", 0)
                usage["total_tokens"] = r_usage.get("total_tokens", usage["prompt_tokens"] + usage["completion_tokens"])
            else:
                usage["prompt_tokens"] = getattr(r_usage, 'input_tokens', 0) or getattr(r_usage, 'prompt_tokens', 0)
                usage["completion_tokens"] = getattr(r_usage, 'output_tokens', 0) or getattr(r_usage, 'completion_tokens', 0)
                usage["total_tokens"] = getattr(r_usage, 'total_tokens', usage["prompt_tokens"] + usage["completion_tokens"])
        return usage

    @staticmethod
    def _wait_for_network():
        """断网自动挂起并轮询，直到恢复"""
        pause_file = "network_pause.txt"
        with open(pause_file, "w", encoding="utf-8") as f:
            f.write(f"Network paused at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\n[!!!] 检测到网络连接故障。程序已挂起，状态已记录至 {pause_file}。")
        print("[*] 将每隔 5 分钟尝试一次联网测试...")

        while True:
            try:
                # 尝试连接一个可靠的地址，例如 OpenRouter 的 API 域名
                import http.client
                conn = http.client.HTTPSConnection("openrouter.ai", timeout=10)
                conn.request("HEAD", "/")
                conn.getresponse()
                conn.close()
                print("[√] 网络已恢复！继续执行...")
                with open(pause_file, "a", encoding="utf-8") as f:
                    f.write(f"Network resumed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                break
            except Exception:
                time.sleep(300) # 等待 5 分钟

    @staticmethod
    def _call_dashscope(model, messages, api_key, temp, result_format, max_tokens, thinking, **kwargs):
        try:
            import dashscope
            from dashscope import Generation
        except ImportError:
            raise ImportError("未安装 dashscope，请运行: pip install dashscope")
        dashscope.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        # 1. 动态构建字典，避免传递None
        call_params = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "result_format": result_format,
            "enable_thinking": thinking
        }
        # 2. 仅当参数不为None时才加入
        if max_tokens is not None:
            call_params["max_tokens"] = max_tokens
        # 3. 合并kwargs
        call_params.update(kwargs)
        # 4. 调用API
        max_retries = 5  # 最大重试次数
        response = None
        for attempt in range(max_retries):
            try:
                response = Generation.call(**call_params)
                # DashScope 特有逻辑：检查是否真正成功。有时网络通了，但服务端返回了5xx错误或被网关拦截
                if response.status_code == 200:
                    break  # 调用完全成功，跳出重试循环
                else:
                    # 如果不是200，说明遇到了API侧的错误（如限流、服务器繁忙），手动抛出异常以触发重试
                    raise Exception(f"DashScope 返回错误状态码: {response.status_code}, 信息: {response.message}")
            except Exception as e:
                error_msg = str(e)
                print(f"[警告] 第 {attempt + 1} 次 DashScope API 调用失败。原因: {error_msg}")
                
                # 识别长时断网类故障
                if any(kw in error_msg for kw in ["Connection error", "getaddrinfo failed", "ConnectError", "timeout", "Failed to resolve"]):
                    MODEL._wait_for_network()
                    # 挂起回来后，我们不增加 attempt 计数，原地重试
                    # 但在 for 循环中很难直接修改计数，简单起见，我们让它继续，反正下一次 attempt 很快就会开始
                
                if attempt == max_retries - 1:
                    # 如果是最后一次尝试依然失败，则抛出异常彻底结束运行
                    raise Exception(f"DashScope API 调用彻底失败 (已重试{max_retries}次): {error_msg}")
                # 指数退避：每次失败后，休眠时间翻倍 (2s, 4s, 8s, 16s...)
                sleep_time = 2 ** (attempt + 1)
                print(f"[*] 等待 {sleep_time} 秒后进行下一次重试...")
                time.sleep(sleep_time)

        usage = MODEL._extract_usage_dashscope(response)

        if thinking:
            result = {"thinking": "", "content": ""}
            if hasattr(response.output, 'choices') and len(response.output.choices) > 0:
                msg = response.output.choices[0].message
                result["thinking"] = getattr(msg, 'reasoning_content', "")
                result["content"] = getattr(msg, 'content', "")
            return result, usage
        else:
            if result_format == "message":
                if hasattr(response.output, 'choices') and len(response.output.choices) > 0:
                    return response.output.choices[0].message.content, usage
                else:
                    return str(response.output), usage
            elif hasattr(response.output, 'text'):
                return response.output.text, usage
            else:
                return str(response.output), usage

    @staticmethod
    def _extract_usage_openrouter(response) -> dict:
        """统一 OpenRouter (OpenAI SDK) 的 token 统计格式"""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if hasattr(response, 'usage') and response.usage:
            usage["prompt_tokens"] = getattr(response.usage, 'prompt_tokens', 0)
            usage["completion_tokens"] = getattr(response.usage, 'completion_tokens', 0)
            usage["total_tokens"] = getattr(response.usage, 'total_tokens', 0)
        return usage

    @staticmethod
    def _call_openrouter(model, messages, api_key, temp, max_tokens, thinking, **kwargs):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("未安装 openai，请运行: pip install openai")

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
        )

        # 1. 动态构建字典，避免传递None
        call_params = {
            "model": model,
            "messages": messages,
            "temperature": temp,
        }
        # 2. 仅当参数不为None时才加入
        if max_tokens is not None:
            call_params["max_tokens"] = max_tokens
        # 处理非标准扩展参数 (OpenRouter特色)
        # 将其放入extra_body以绕过OpenAI SDK的参数检查
        extra_body = {}
        if thinking:
            extra_body["reasoning"] = {"enabled": True }
            extra_body["include_reasoning"] = True
        # 允许用户通过kwargs传入其他扩展参数(如provider偏好等)
        if "extra_body" in kwargs:
            extra_body.update(kwargs.pop("extra_body"))
        if extra_body:
            call_params["extra_body"] = extra_body
        # 3. 合并kwargs
        call_params.update(kwargs)
        # 4. 调用API
        max_retries = 5  # 最大重试次数
        response = None
        for attempt in range(max_retries):
            try:
                print('messages'*10)
                print(messages)
                print('messages'*10)
                response = client.chat.completions.create(**call_params)
                print('response'*10)
                print(response)
                print('response'*10)
                # 处理响应，OpenRouter返回的是OpenAI定义的ChatCompletion对象
                if not response or not response.choices:
                    raise Exception("OpenRouter API 返回了空响应")
                break  # 调用成功，立刻跳出重试循环
            except Exception as e:
                error_msg = str(e)
                print(f"[警告] 第 {attempt + 1} 次 OpenRouter API 调用失败。原因: {error_msg}")

                # 识别长时断网类故障
                if any(kw in error_msg for kw in ["Connection error", "getaddrinfo failed", "ConnectError", "timeout", "Failed to resolve"]):
                    MODEL._wait_for_network()

                if attempt == max_retries - 1:
                    # 如果是最后一次尝试依然失败，则抛出异常结束运行
                    raise Exception(f"OpenRouter API 调用彻底失败 (已重试{max_retries}次): {error_msg}")
                # 指数退避：每次失败后，休眠时间翻倍 (2s, 4s, 8s, 16s...)
                sleep_time = 2 ** (attempt + 1)
                print(f"[*] 等待 {sleep_time} 秒后进行下一次重试...")
                time.sleep(sleep_time)

        msg = response.choices[0].message
        usage = MODEL._extract_usage_openrouter(response)

        if thinking:
            reasoning = (
                getattr(msg, 'reasoning', None) or 
                getattr(msg, 'reasoning_content', None)
            )
            # 有些 Provider 会把思考过程直接塞在 content 的 <think> 标签里
            content = msg.content or ""
            if not reasoning and "<think>" in content:
                import re
                think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
                if think_match:
                    reasoning = think_match.group(1).strip()
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            return {
                "thinking": reasoning or "", 
                "content": content
            }, usage
        else:
            return msg.content, usage

    @staticmethod
    def forward(
        query: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        sys_prompt: Optional[str] = None,
        model_name: str = "deepseek/deepseek-chat", # "deepseek-v3", "deepseek-v3.2" for DashScope, "deepseek/deepseek-chat", "deepseek/deepseek-v3.2" for OpenRouter
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        enable_thinking: bool = False,
        result_format: str = "message",
        **kwargs
    ) -> Tuple[Union[str, Dict], Dict[str, int]]:
        provider = MODEL._get_provider(api_key)
        final_messages = MODEL._build_messages(query, messages, sys_prompt)

        if provider == "dashscope":
            return MODEL._call_dashscope(model_name, final_messages, api_key, temperature, result_format, max_tokens, enable_thinking, **kwargs)
        elif provider == "openrouter":
            return MODEL._call_openrouter(model_name, final_messages, api_key, temperature, max_tokens, enable_thinking, **kwargs)
        else:
            raise ValueError("未知的 API 提供商")


# 使用示例
if __name__ == "__main__":
    # 示例1：基本调用
    print("示例1：基本调用")
    response = MODEL.forward(
        query="你是谁？",
        model_name="google/gemini-2.5-pro"    # deepseek-v3 for dashscope, deepseek/deepseek-chat for openrouter
    )
    print(response)
    print("\n" + "="*50 + "\n")
    
    # 示例2：使用思考模式
    print("示例2：使用思考模式")
    result = MODEL.forward(
        query="你是谁？",
        model_name="google/gemini-2.5-pro", # deepseek-v3.2 for dashscope, deepseek/deepseek-v3.2 for openrouter
        enable_thinking=True
    )
    if result["thinking"]:
        print("思考过程：")
        print(result["thinking"])
        print("\n" + "-"*50 + "\n")
    print("回答内容：")
    print(result["content"])
    print("\n" + "="*50 + "\n")
    
    # 示例3：使用messages
    print("示例3：使用messages")
    messages = [
        {"role": "system", "content": "你是一个专业的编程助手"},
        {"role": "user", "content": "如何用Python读取CSV文件？"}
    ]
    response = MODEL.forward(
        messages=messages,
        model_name="google/gemini-2.5-pro"
    )
    print(response)