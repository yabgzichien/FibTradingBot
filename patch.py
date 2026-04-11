import re

def patch_backtest():
    with open('backtest.py', 'r', encoding='utf-8') as f:
        content = f.read()

    new_block = """            if pending and not in_position and (pending_active_from_i is None or i >= pending_active_from_i):
                bar_o = row['open']
                bar_h = row['high']
                bar_l = row['low']
                path_o, path_first, path_second = _bar_sequence(bar_o, bar_h, bar_l)
                
                prev_c = df['close'].iloc[i-1] if i > 0 else bar_o
                
                is_triggered = False
                actual_fill = pending_entry
                trigger_path_first = None
                
                if pending_entry <= prev_c:
                    if bar_l <= pending_entry:
                        is_triggered = True
                        actual_fill = bar_o if bar_o <= pending_entry else pending_entry
                        trigger_path_first = bar_l
                else:
                    if bar_h >= pending_entry:
                        is_triggered = True
                        actual_fill = bar_o if bar_o >= pending_entry else pending_entry
                        trigger_path_first = bar_h

                if is_triggered:
                    if pending_dir == 1:
                        execution_entry = actual_fill + entry_cost_points
                        sl_dist = execution_entry - pending_sl
                        if sl_dist > 0:
                            base_balance = initial_balance if risk_base == "initial" else balance
                            min_sl_dist = float(min_sl_points) * point_value
                            safe_sl_dist = max(sl_dist, min_sl_dist) if min_sl_dist > 0 else sl_dist
                            position_size = (base_balance * risk_per_trade) / safe_sl_dist
                            in_position = True
                            position_type = 1
                            entry_price = execution_entry
                            sl_price = pending_sl
                            tp_price = pending_tp
                            entry_time = index
                    else:
                        execution_entry = actual_fill - entry_cost_points
                        sl_dist = pending_sl - execution_entry
                        if sl_dist > 0:
                            base_balance = initial_balance if risk_base == "initial" else balance
                            min_sl_dist = float(min_sl_points) * point_value
                            safe_sl_dist = max(sl_dist, min_sl_dist) if min_sl_dist > 0 else sl_dist
                            position_size = (base_balance * risk_per_trade) / safe_sl_dist
                            in_position = True
                            position_type = -1
                            entry_price = execution_entry
                            sl_price = pending_sl
                            tp_price = pending_tp
                            entry_time = index

                    pending = False
                    pending_setup_id = None
                    pending_since_i = None
                    pending_active_from_i = None

                    if in_position:
                        # ── Intra-bar exit check with path heuristic ─────────────
                        if position_type == 1:
                            sl_hit = bar_l <= sl_price
                            tp_hit = bar_h >= tp_price
                            
                            if trigger_path_first == bar_l:
                                sl_reachable = sl_hit
                                tp_reachable = tp_hit and (path_first == bar_l or (pending_entry <= prev_c and bar_o <= pending_entry))
                            else:
                                sl_reachable = sl_hit and (path_first == bar_h or (pending_entry > prev_c and bar_o >= pending_entry))
                                tp_reachable = tp_hit
                        else:  # Short
                            sl_hit = bar_h >= sl_price
                            tp_hit = bar_l <= tp_price
                            
                            if trigger_path_first == bar_l:
                                sl_reachable = sl_hit and (path_first == bar_l or (pending_entry <= prev_c and bar_o <= pending_entry))
                                tp_reachable = tp_hit
                            else:
                                sl_reachable = sl_hit
                                tp_reachable = tp_hit and (path_first == bar_h or (pending_entry > prev_c and bar_o >= pending_entry))

                        if not allow_entry_bar_tp:
                            tp_reachable = False
                            
                        if sl_reachable and tp_reachable:
                            if both_hit_policy == "tp":
                                execution_exit = (tp_price - exit_cost_points) if position_type == 1 else (tp_price + exit_cost_points)
                                pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                                balance += pnl
                                trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Win'})
                                in_position = False
                            else:
                                execution_exit = (sl_price - exit_cost_points) if position_type == 1 else (sl_price + exit_cost_points)
                                pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                                balance += pnl
                                trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Loss'})
                                in_position = False
                        elif sl_reachable:
                            execution_exit = (sl_price - exit_cost_points) if position_type == 1 else (sl_price + exit_cost_points)
                            pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                            balance += pnl
                            trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Loss'})
                            in_position = False
                        elif tp_reachable:
                            execution_exit = (tp_price - exit_cost_points) if position_type == 1 else (tp_price + exit_cost_points)
                            pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                            balance += pnl
                            trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Win'})
                            in_position = False"""

    start_str = "            if pending and not in_position and (pending_active_from_i is None or i >= pending_active_from_i):"
    end_str = "            elif (not pending) and (not in_position):"
    
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx == -1 or end_idx == -1:
        print("COULD NOT FIND ANCHORS")
        return
        
    updated = content[:start_idx] + new_block + "\n" + content[end_idx:]
    
    with open('backtest.py', 'w', encoding='utf-8') as f:
        f.write(updated)
        
    print("Patched backtest.py")

if __name__ == "__main__":
    patch_backtest()
