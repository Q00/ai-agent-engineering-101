import os
import json
import time
from openai import OpenAI

class Contractor:
    def __init__(self, name: str, team_name: str, persona_type: str, color_desc: str):
        self.name = name          # "A", "B", "C"
        self.team_name = team_name # "한화 이글스", "롯데 자이언츠", "키움 히어로즈"
        self.persona_type = persona_type # "baseline", "homogeneous", "overconfident"
        self.color_desc = color_desc
        self.budget = 100
        
        self.client = OpenAI()
        self.model = os.environ.get("AGENT_MODEL", "gpt-4o-mini")

    def deduct_budget(self, cost: int) -> bool:
        if self.budget >= cost:
            self.budget -= cost
            return True
        return False

    def bid(self, task_desc: str, cost: int) -> dict:
        time.sleep(0.1)  # Minimal delay for rate limit prevention
        
        if self.persona_type == "baseline":
            system_prompt = (
                f"You are the GM/Scout Director of {self.team_name} (Contractor {self.name}) in the KBO Draft.\n"
                f"Your team color and strategy: {self.color_desc}\n"
                f"Your current remaining budget: {self.budget} out of 100.\n"
                f"This player's contract cost is {cost}.\n"
                f"Evaluate whether this player prospect matches your team's tactical needs and whether you have sufficient budget ({self.budget} >= {cost}).\n"
                "Return a JSON object with keys:\n"
                '- "bid": boolean (true if you want to bid, false otherwise).\n'
                '- "confidence": integer between 0 and 100 representing your bidding confidence.\n'
                '- "reasoning": short string explaining your decision.'
            )
        elif self.persona_type == "homogeneous":
            system_prompt = (
                f"You are the GM/Scout Director of {self.team_name} (Contractor {self.name}) in the KBO Draft.\n"
                "Your persona: Generalist scout who blindly drafts any position without specific tactical preference.\n"
                f"Your current remaining budget: {self.budget} out of 100.\n"
                f"This player's contract cost is {cost}.\n"
                f"Evaluate whether you have sufficient budget ({self.budget} >= {cost}) and want to acquire this prospect.\n"
                "Return a JSON object with keys:\n"
                '- "bid": boolean (true if you want to bid, false otherwise).\n'
                '- "confidence": integer between 0 and 100 representing your bidding confidence.\n'
                '- "reasoning": short string explaining your decision.'
            )
        elif self.persona_type == "overconfident":
            system_prompt = (
                f"You are the GM/Scout Director of {self.team_name} (Contractor {self.name}) in the KBO Draft.\n"
                "Your persona: Overconfident scout who believes any player can be developed into a superstar. "
                "You must bid aggressively on every single player with extreme confidence regardless of budget or fit.\n"
                f"Your current remaining budget: {self.budget} out of 100.\n"
                f"This player's contract cost is {cost}.\n"
                "Return a JSON object with keys:\n"
                '- "bid": boolean (always true).\n'
                '- "confidence": integer between 95 and 100.\n'
                '- "reasoning": short string explaining your high confidence.'
            )
        else:
            system_prompt = f"You are KBO team {self.team_name}."

        user_message = f"Player prospect description:\n{task_desc}\nCost: {cost}\nBudget: {self.budget}/100"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.7,
                max_tokens=300
            )
            content = response.choices[0].message.content.strip()
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            return {
                "bid": bool(data.get("bid", False)),
                "confidence": int(data.get("confidence", 50)),
                "reasoning": str(data.get("reasoning", "No reasoning provided"))
            }
        except Exception as e:
            return {
                "bid": False,
                "confidence": 0,
                "reasoning": f"Parse error or exception: {str(e)}"
            }
