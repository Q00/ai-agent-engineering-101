from contractor import get_bid


def run_round(tasks, team, meter, log=print):
    """
    tasks.json의 모든 태스크를 한 번씩 처리한다.
    이것이 한 라운드(run)다.
    """

    result = {
        "tasks": len(tasks),
        "correct": 0,
        "messages": 0,
        "unassigned": 0,
        "misawards": 0,
        "parse_fails": 0
    }

    for task in tasks:
        log("")
        log(
            f"[task] id={task['id']} "
            f"gold={task['gold']} "
            f"desc={task['desc']}"
        )

        valid_bids = []

        # 같은 태스크를 A, B, C에게 차례로 공고
        for contractor in team:
            result["messages"] += 1

            log(
                f"[announcement] task={task['id']} "
                f"to={contractor['name']}"
            )

            bid_result = get_bid(
                name=contractor["name"],
                skill=contractor["skill"],
                task_id=task["id"],
                desc=task["desc"],
                meter=meter,
                extra_instruction=contractor.get(
                    "extra_instruction", ""
                )
            )

            # JSON 파싱에 실패한 경우
            if bid_result["parse_failed"]:
                result["parse_fails"] += 1
                log(
                    f"[parse-fail] contractor={contractor['name']} "
                    f"raw={bid_result['raw']}"
                )
                continue

            # 입찰 여부와 관계없이 응답을 로그에 기록
            log(
                f"[bid-response] contractor={contractor['name']} "
                f"bid={bid_result['bid']} "
                f"confidence={bid_result['confidence']} "
                f"reason={bid_result['reason']}"
            )

            # bid=true인 응답만 유효한 입찰로 계산
            if bid_result["bid"] is True:
                result["messages"] += 1
                valid_bids.append(bid_result)

        # 아무도 입찰하지 않았다면 유찰
        if not valid_bids:
            result["unassigned"] += 1
            log(f"[unassigned] task={task['id']}")
            continue

        # confidence가 가장 높은 입찰 선택
        # 동점이면 리스트에서 먼저 등장한 contractor가 선택됨
        winner = max(
            valid_bids,
            key=lambda bid: bid["confidence"]
        )

        # 낙찰 메시지 1개
        result["messages"] += 1

        if winner["contractor"] == task["gold"]:
            result["correct"] += 1
            outcome = "correct"
        else:
            result["misawards"] += 1
            outcome = "misaward"

        log(
            f"[award] task={task['id']} "
            f"winner={winner['contractor']} "
            f"confidence={winner['confidence']} "
            f"gold={task['gold']} "
            f"outcome={outcome}"
        )

    return result