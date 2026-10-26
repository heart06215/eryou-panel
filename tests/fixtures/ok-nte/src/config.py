config = {
    "gui_title": "ok-nte",
    "log_file": "logs/ok-nte.log",
    "onetime_tasks": [
        ["src.tasks.LauncherTask", "LauncherTask"],
        ["src.tasks.daily.DailyRoutineTask", "DailyRoutineTask"],
        ["src.tasks.FishingTask", "FishingTask"],
        ["src.tasks.RhythmTask", "RhythmTask"],
        ["ok", "DiagnosisTask"],
    ],
    "trigger_tasks": [
        ["src.tasks.trigger.AutoCombatTask", "AutoCombatTask"],
        ["src.tasks.trigger.SkipDialogTask", "SkipDialogTask"],
    ],
}
