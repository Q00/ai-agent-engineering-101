import os
import csv
import json
from pathlib import Path
from contractor import Contractor

TEAM_CONFIGS = {
    "A": {
        "team_name": "한화 이글스",
        "baseline_color": "강속구 선발투수 및 우타 거포 영입에 집중"
    },
    "B": {
        "team_name": "롯데 자이언츠",
        "baseline_color": "센터라인 내야수 및 위력적인 마무리 투수 영입에 집중"
    },
    "C": {
        "team_name": "키움 히어로즈",
        "baseline_color": "5툴 외야수 및 고효율 저비용 유망주 영입에 집중"
    }
}

CONDITIONS = ["baseline", "homogeneous", "overconfident"]
RUNS_PER_CONDITION = 3
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]

def run_draft():
    sub_dir = Path(__file__).parent
    logs_dir = sub_dir / "logs"
    logs_dir.mkdir(exist_ok=True, parents=True)
    
    tasks_file = sub_dir / "tasks.json"
    if not tasks_file.is_file():
        raise FileNotFoundError("tasks.json not found in submission directory.")
    
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    results_file = sub_dir / "results.csv"
    
    # Initialize results.csv with header
    with results_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
    
    run_counter = 1
    
    for condition in CONDITIONS:
        for r_idx in range(RUNS_PER_CONDITION):
            run_id = f"run-{run_counter:02d}"
            log_path = logs_dir / f"{run_id}.log"
            
            log_lines = []
            log_lines.append(f"=== KBO Draft Experiment: Condition={condition}, Run={run_id} ===")
            
            contractors = {
                "A": Contractor("A", TEAM_CONFIGS["A"]["team_name"], condition, TEAM_CONFIGS["A"]["baseline_color"]),
                "B": Contractor("B", TEAM_CONFIGS["B"]["team_name"], condition, TEAM_CONFIGS["B"]["baseline_color"]),
                "C": Contractor("C", TEAM_CONFIGS["C"]["team_name"], condition, TEAM_CONFIGS["C"]["baseline_color"]),
            }
            
            total_tasks = len(tasks)
            correct_count = 0
            messages_count = 0
            unassigned_count = 0
            misawards_count = 0
            error_note = ""
            
            try:
                for idx, task in enumerate(tasks, start=1):
                    t_id = task["id"]
                    t_desc = task["desc"]
                    t_gold = task["gold"]
                    t_cost = task["cost"]
                    
                    log_lines.append(f"\n--- Task {idx}: {t_id} (Cost: {t_cost}, Gold: {t_gold}) ---")
                    log_lines.append(f"[Announcement] Manager announced prospect: {t_desc} (Cost: {t_cost})")
                    
                    messages_count += 3 # 3 announcements
                    
                    bids = {}
                    for c_id, contractor in contractors.items():
                        log_lines.append(f"[Announcement Sent] To Contractor {c_id} ({contractor.team_name}), Budget: {contractor.budget}/100")
                        
                        bid_res = contractor.bid(t_desc, t_cost)
                        bids[c_id] = bid_res
                        
                        messages_count += 1 # 1 bid per contractor
                        
                        log_lines.append(
                            f"[Bid Received] Contractor {c_id}: bid={bid_res['bid']}, "
                            f"confidence={bid_res['confidence']}, reasoning='{bid_res['reasoning']}', "
                            f"budget_remaining={contractor.budget}"
                        )
                    
                    valid_bids = {}
                    for c_id, b in bids.items():
                        if b["bid"] and contractors[c_id].budget >= t_cost:
                            valid_bids[c_id] = b
                        elif b["bid"] and contractors[c_id].budget < t_cost:
                            log_lines.append(f"[Disqualification] Contractor {c_id} bid True but has insufficient budget ({contractor.budget} < {t_cost})")
                    
                    if not valid_bids:
                        unassigned_count += 1
                        log_lines.append(f"[Award] Task {t_id} unassigned (No valid bids or insufficient budget).")
                    else:
                        winner_id = max(valid_bids.keys(), key=lambda k: (valid_bids[k]["confidence"], -ord(k)))
                        winning_bid = valid_bids[winner_id]
                        
                        success = contractors[winner_id].deduct_budget(t_cost)
                        if success:
                            log_lines.append(
                                f"[Award] Task {t_id} awarded to Contractor {winner_id} "
                                f"({contractors[winner_id].team_name}) with confidence {winning_bid['confidence']}. "
                                f"New budget: {contractors[winner_id].budget}/100"
                            )
                            if winner_id == t_gold:
                                correct_count += 1
                                log_lines.append(f"[Evaluation] Correct award to gold contractor {t_gold}.")
                            else:
                                misawards_count += 1
                                log_lines.append(f"[Evaluation] Misaward! Awarded to {winner_id}, but gold was {t_gold}.")
                        else:
                            unassigned_count += 1
                            log_lines.append(f"[Award] Task {t_id} unassigned due to budget deduction failure for winner {winner_id}.")
                    
                    messages_count += 1 # 1 award notification
                    log_lines.append(f"[Award Message Sent] Manager notified all contractors of round result.")
                
            except Exception as e:
                error_note = str(e)
                log_lines.append(f"[CRASH] Run crashed with error: {error_note}")
                
            log_path.write_text("\n".join(log_lines), encoding="utf-8")
            
            if error_note:
                row = [run_id, condition, "", "", "", "", "", error_note]
            else:
                row = [run_id, condition, str(total_tasks), str(correct_count), str(messages_count), str(unassigned_count), str(misawards_count), ""]
            
            # Append row incrementally to results.csv
            with results_file.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(row)
            
            print(f"Finished {run_id} ({condition}) -> correct={correct_count}, unassigned={unassigned_count}, misawards={misawards_count}")
            run_counter += 1
            
    print(f"Successfully generated results.csv and {run_counter-1} log files.")

if __name__ == "__main__":
    run_draft()
