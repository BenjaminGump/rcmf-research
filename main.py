import argparse
import shutil
from agent import AppWorldAgent
from dataset import load_appworld
from pathlib import Path
import json
from datetime import datetime
import time

def main():
    parser = argparse.ArgumentParser(description="Run AppWorld experiments.")
    parser.add_argument("--dataset_split", type=str, default="train", choices=["train", "val", "test"], help="Dataset split.")
    args = parser.parse_args()

    root = "."
    use_rcmf = True
    dataset_split = args.dataset_split

    continue_running = False  # 是否开启普通断点续测（跳过所有已有结果）
    test_pass_k = False       # 是否开启 pass@k 模式（优先级高于 continue_running：仅跳过 successful 的，重试 failed 的）
    
    # 如果开启断点续测或 pass@k，请在这里填入你要继续的实验文件夹名称，比如cognition_test_20260420153647
    # 如果保持为空字符串 ""，则忽略断点续测和 pass@k，开启全新实验
    resume_experiment_name = "react_train_20260605110305" 

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    framework = 'RCMF' if use_rcmf else 'ReAct'
    
    # 判断是否恢复历史实验 (pass@k 或 断点续传)
    if (continue_running or test_pass_k) and resume_experiment_name:
        experiment_name = resume_experiment_name
        mode_str = "pass@k 重试模式" if test_pass_k else "断点续测模式"
        print(f"启用了 {mode_str}，将基于此实验进行: {experiment_name}")
    else:
        experiment_name = f"{framework}_{dataset_split}_{timestamp}"
        print(f"开始全新实验: {experiment_name}")

    train_data, val_data, test_data = load_appworld(
        data_path="data/tasks",
        train_size=90,
        val_size=57,
        test_size=168,  # test_normal全量
        seed=1
    )

    if dataset_split == "train":
        split = train_data
    elif dataset_split == "val":
        split = val_data
    elif dataset_split == "test":
        split = test_data
    else:
        raise ValueError("Invalid dataset split. Choose from 'train', 'val', or 'test'.")

    # 定义各种输出路径
    experiment_dir = Path(root) / "experiments" / "outputs" / experiment_name
    evaluations_dir = experiment_dir / "evaluations"

    correct_cnt = 0
    processed_cnt = 0
    skipped_cnt = 0
    retried_cnt = 0

    # 添加一个变量，专门用来控制是否命中断点续测的那个 task_id，方便在pass@k的时候使用断点续测功能
    # 如果不是在测试pass@k的过程中使用断点续测的功能，则将target_resume_task设置为 None 或 "" 即可，代码会自动跳过断点续测的逻辑
    target_resume_task = ""  # 填入崩溃的那个 task_id
    hit_resume_task = False if target_resume_task else True

    for task_id in split:
        processed_cnt += 1
        eval_json_path = evaluations_dir / f"{task_id}.json"
        # 动态决定当前 task 的 pass@k 策略
        current_test_pass_k = test_pass_k
        if not hit_resume_task:
            if task_id == target_resume_task:
                hit_resume_task = True
                print(f"\n====== 到达指定断点 Task ID: {task_id}，恢复 pass@k 重试模式 ======\n")
            else:
                # 在遇到断点前，强制关闭 pass@k 重试，使其只作为普通断点续测（读取统计后跳过）
                current_test_pass_k = False
        # 断点续测核心逻辑：检查是否已存在评估JSON文件
        if (continue_running or current_test_pass_k) and eval_json_path.exists():
            # 读取历史JSON结果提取is_correct
            try:
                with open(eval_json_path, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
                is_correct_history = history_data.get("is_correct", False)
                if current_test_pass_k:
                    # pass@k 逻辑：历史成功的跳过，历史失败的重试
                    if is_correct_history:
                        skipped_cnt += 1
                        correct_cnt += 1
                        current_acc = (correct_cnt / processed_cnt) * 100
                        print(f"[{processed_cnt}/{len(split)} 任务进度] pass@k 模式: 发现历史成功记录，跳过 Task ID: {task_id} | 当前总体 Accuracy: {current_acc:.2f}%")
                        continue
                    else:
                        retried_cnt += 1
                        print(f"[{processed_cnt}/{len(split)} 任务进度] pass@k 模式: 发现历史失败记录，开始重试 Task ID: {task_id}")
                        # 不 continue，让代码继续向下走到 agent.execute 进行重试
                elif continue_running:
                    # 普通断点续传逻辑：只要有历史记录就跳过
                    skipped_cnt += 1
                    if is_correct_history:
                        correct_cnt += 1
                    current_acc = (correct_cnt / processed_cnt) * 100
                    print(f"[{processed_cnt}/{len(split)} 任务进度] 发现历史记录，跳过 Task ID: {task_id} | 历史 Success: {is_correct_history} | 当前总体 Accuracy: {current_acc:.2f}%")
                    continue
            except Exception as e:
                # 防止由于文件损坏或写入未完成导致的 JSON 解析错误卡死整个流程
                print(f"[{processed_cnt}/{len(split)} 任务进度] 发现历史记录，但读取或解析 {eval_json_path.name} 失败: {e}。重新运行该任务 Task ID: {task_id}")

        # 正常运行 Agent
        t0 = time.time()
        agent = AppWorldAgent(dataset_name="appworld", experiment_name=experiment_name)
        is_correct, trace = agent.execute(task_id=task_id)
        t1 = time.time()
        time_cost = t1 - t0
        correct_cnt = correct_cnt + (1 if is_correct else 0)
        current_acc = (correct_cnt / processed_cnt) * 100
        print(f"[{processed_cnt}/{len(split)} 任务进度] Task ID: {task_id} | Success: {is_correct} | 当前总体 Accuracy: {current_acc:.2f}% | 本题耗时: {time_cost:.2f}s")

    # 所有的任务跑完后，计算最终胜率
    total_tasks = len(split)
    accuracy = (correct_cnt / total_tasks) * 100 if total_tasks > 0 else 0

    log_path = experiment_dir / "final_results.json"
    log_data = {
        "total": total_tasks,
        "correct": correct_cnt,
        "accuracy": accuracy,
        "skipped_in_this_run": skipped_cnt,
        "retried_in_this_run": retried_cnt
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=4, ensure_ascii=False)
    
    print("\n" + "="*30)
    print(f"评估完成！")
    print(f"总任务数: {total_tasks}")
    print(f"本次运行跳过(读取历史)任务数: {skipped_cnt}")
    print(f"本次运行重试(覆盖历史失败)任务数: {retried_cnt}")
    print(f"成功总次数 (含历史成功及本次重试成功): {correct_cnt}")
    print(f"最终准确率: {accuracy:.2f}%")
    print("="*30)

    # 复制 cognition/ 文件夹并重命名为 experiment_name
    cognition_src = Path(root) / "cognition"
    cognition_dst = Path(root) / experiment_name
    if cognition_src.exists():
        print(f"正在保存 cognition 状态到: {experiment_name}")
        shutil.copytree(cognition_src, cognition_dst, dirs_exist_ok=True)
    else:
        print("未找到 cognition 文件夹，跳过保存。")


if __name__ == "__main__":
    main()