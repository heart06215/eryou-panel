config = {
    "log_file": "logs/ok-ww.log",
    "onetime_tasks": [
        ["src.task.DailyTask2", "DailyTask2"],
        ["src.task.FarmEchoTask2", "FarmEchoTask2"],
        ["src.task.GardenTask", "GardenTask"],
    ],
    "trigger_tasks": [
        ["src.task.AutoCombatTask", "AutoCombatTask"],
    ],
}
