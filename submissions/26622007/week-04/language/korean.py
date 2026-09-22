"""Load isolated copies of the original protocol; translate only the prompt constants."""
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lab"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = load_module("korean_html_protocol", ROOT / "lab/experiment.py")
lab.ROLE = {
    "buyer": "당신은 {item}의 구매자이며, 판매자와 가격을 협상하고 있습니다. "
             "당신의 비공개 한도: 최대 {limit}까지 지불할 수 있습니다. {limit}보다 높은 가격에는 절대 동의하지 마세요.",
    "seller": "당신은 {item}의 판매자입니다. 최소 {limit}의 가격을 수락할 수 있습니다. "
              "{limit}보다 낮은 가격에는 절대 동의하지 마세요.",
}
lab.COMMON = (" 사용할 수 있는 화행은 네 가지입니다: propose (가격 제안), "
              "accept-proposal (상대의 마지막 가격에 동의하며, 합의로 협상 종료), "
              "reject-proposal (마지막 가격을 거절하고 협상 계속), refuse (협상을 완전히 포기하며, 거래 없이 종료).")
lab.FORMAT = {
    "free": " 메시지를 일반적인 한국어 문장 한두 개로 작성하세요.",
    "tagged": " 메시지를 괄호로 감싼 화행 태그 하나로 정확히 시작하세요. 태그는 (propose), "
              "(accept-proposal), (reject-proposal), (refuse) 중 하나이며, 그 뒤에 일반적인 한국어 문장 하나를 작성하세요.",
    "structured": ' JSON 객체 하나만 출력하고 다른 것은 출력하지 마세요: {"performative": "propose" | '
                  '"accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <정수 또는 null>}}.',
}
lab.READER_SYSTEM = ("당신은 구매자와 판매자 사이의 가격 협상을 읽는 관찰자입니다. "
                     "마지막 메시지만 분류하세요. JSON 객체 하나만 출력하고 다른 것은 출력하지 마세요: "
                     '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <정수 또는 null>}.')
ITEMS = {1: "중고 자전거", 2: "탁상등", 3: "중고 교재", 4: "기계식 키보드"}
unlimited = load_module("korean_unlimited_protocol", ROOT / "turn_limit/run_unlimited.py")
# Each module has its own namespace; this never mutates the English experiment module.
unlimited.lab = lab
