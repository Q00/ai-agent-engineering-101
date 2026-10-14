def build_team(condition):
    skills = {
        "A": "arithmetic",
        "B": "English writing",
        "C": "Python coding",
    }

    if condition == "homogeneous":
        skills = {name: "general problem solving" for name in skills}

    team = []

    for name, skill in skills.items():
        team.append({
            "name": name,
            "skill": skill,
            "overconfident": (
                condition == "overconfident" and name == "C"
            ),
        })

    return team


if __name__ == "__main__":
    for condition in ["baseline", "homogeneous", "overconfident"]:
        print(f"\nCondition: {condition}")
        for contractor in build_team(condition):
            print(contractor)