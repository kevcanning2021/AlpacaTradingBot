import config.settings as s
from scheduler import MarketHoursScheduler

print('CHECK_INTERVAL_MINUTES', s.CHECK_INTERVAL_MINUTES)
print('ENABLE_STOP_LOSS_ADJUSTMENT', s.ENABLE_STOP_LOSS_ADJUSTMENT)
print('ENABLE_REENTRY', s.ENABLE_REENTRY)

sched = MarketHoursScheduler()
print('scheduler created')
print('jobs before start', len(sched.scheduler.get_jobs()))
