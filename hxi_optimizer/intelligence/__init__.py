"""Intelligence layer — turns ML output into decisions for Steve.

The classifier answers *what* is happening. The intelligence layer answers
*so what, and what should Steve do about it*. Each module produces
human-readable assessments grounded in the current machine's specs + context.

Contains:
    diagnosis           Rule-fused-with-ML engine: metrics + specs -> Diagnosis
    trend_analyzer      Multi-day rolling statistics; detect slow drift
    fleet_triage        Rank N rigs by "attention needed" score
    digest              Plain-language summaries (hourly / daily)
"""
