import time


def send_with_retry(chat, log=print):
    delays = [2, 4, 8, 16, 32]

    for attempt in range(len(delays) + 1):
        try:
            return chat.send()
        except Exception as error:
            is_rate_limit = getattr(error, "status_code", None) == 429

            if not is_rate_limit or attempt == len(delays):
                raise

            delay = delays[attempt]
            log(
                f"[retry] HTTP 429; waiting {delay} seconds "
                f"before retry {attempt + 1}/{len(delays)}"
            )
            time.sleep(delay)