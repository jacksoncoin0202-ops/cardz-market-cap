# Retired deliberately. GemRate must run inside the single CARDZ parent task so
# price, population, ranking and pointer publication share one run id and one
# fail-closed generation. Independent tasks can create mixed-date rankings.
throw 'This installer is retired. Use pipelines\install_daily_task.ps1; the parent job now runs GemRate and SNK itself.'
