class DispatchPolicy:
  def __init__(self):
    pass
  def take_action(self, hour_of_day, current_price, firm_load_mw, arriving_flex_mw,
                  backlog_mwh, oldest_backlog_age_h, battery_soc_mwh,
                  battery_capacity_mwh, battery_power_mw):
    return arriving_flex_mw, 0.0
