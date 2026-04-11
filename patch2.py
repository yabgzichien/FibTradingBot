import re

def patch_backtest():
    with open('backtest.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Add invert_signals parameter.
    sig_old = 'return_events=False,\n    save_csv=True\n):'
    sig_new = 'return_events=False,\n    save_csv=True,\n    invert_signals=False\n):'
    content = content.replace(sig_old, sig_new)

    # 2. Patch the entry logic block and intra-bar check.
    # Lines 477 to 625:
    
    start_str = "            if pending and not in_position and (pending_active_from_i is None or i >= pending_active_from_i):"
    end_str = "            elif (not pending) and (not in_position):"
    
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx == -1 or end_idx == -1:
        print("COULD NOT FIND ANCHORS")
        return

    new_block = """            if pending and not in_position and (pending_active_from_i is None or i >= pending_active_from_i):
                bar_o = row['open']
                bar_h = row['high']
                bar_l = row['low']
                path_o, path_first, path_second = _bar_sequence(bar_o, bar_h, bar_l)

                # Evaluator for trigger condition purely based on original pending_dir
                is_triggered = False
                trigger_path_first = None
                actual_fill = pending_entry

                if pending_dir == 1:  # Original Long
                    if bar_l <= pending_entry:
                        is_triggered = True
                        actual_fill = bar_o if bar_o <= pending_entry else pending_entry
                        trigger_path_first = bar_l
                else:  # Original Short
                    if bar_h >= pending_entry:
                        is_triggered = True
                        actual_fill = bar_o if bar_o >= pending_entry else pending_entry
                        trigger_path_first = bar_h

                if is_triggered:
                    opened_dir = pending_dir
                    sl_level_target = pending_sl
                    tp_level_target = pending_tp
                    
                    if invert_signals:
                        opened_dir = -pending_dir
                        sl_level_target = pending_tp
                        tp_level_target = pending_sl

                    if opened_dir == 1:
                        execution_entry = actual_fill + entry_cost_points
                        sl_dist = execution_entry - sl_level_target
                        if sl_dist > 0:
                            base_balance = initial_balance if risk_base == "initial" else balance
                            min_sl_dist = float(min_sl_points) * point_value
                            safe_sl_dist = max(sl_dist, min_sl_dist) if min_sl_dist > 0 else sl_dist
                            position_size = (base_balance * risk_per_trade) / safe_sl_dist
                            in_position = True
                            position_type = 1
                            entry_price = execution_entry
                            sl_price = sl_level_target
                            tp_price = tp_level_target
                            entry_time = index
                    else:
                        execution_entry = actual_fill - entry_cost_points
                        sl_dist = sl_level_target - execution_entry
                        if sl_dist > 0:
                            base_balance = initial_balance if risk_base == "initial" else balance
                            min_sl_dist = float(min_sl_points) * point_value
                            safe_sl_dist = max(sl_dist, min_sl_dist) if min_sl_dist > 0 else sl_dist
                            position_size = (base_balance * risk_per_trade) / safe_sl_dist
                            in_position = True
                            position_type = -1
                            entry_price = execution_entry
                            sl_price = sl_level_target
                            tp_price = tp_level_target
                            entry_time = index
                            
                    pending = False
                    pending_setup_id = None
                    pending_since_i = None
                    pending_active_from_i = None

                    if in_position:
                        # ── Chronological Intra-bar check ─────────────
                        sl_reachable = False
                        tp_reachable = False
                        
                        if position_type == 1:
                            sl_hit = bar_l <= sl_price
                            tp_hit = bar_h >= tp_price
                            if trigger_path_first == bar_l:
                                sl_reachable = sl_hit
                                tp_reachable = tp_hit and (path_first == bar_l or bar_o <= pending_entry)
                            else:
                                sl_reachable = sl_hit and (path_first == bar_h or bar_o >= pending_entry)
                                tp_reachable = tp_hit
                        else:  # Short
                            sl_hit = bar_h >= sl_price
                            tp_hit = bar_l <= tp_price
                            if trigger_path_first == bar_l:
                                sl_reachable = sl_hit and (path_first == bar_l or bar_o <= pending_entry)
                                tp_reachable = tp_hit
                            else:
                                sl_reachable = sl_hit
                                tp_reachable = tp_hit and (path_first == bar_h or bar_o >= pending_entry)

                        if not allow_entry_bar_tp:
                            tp_reachable = False
                            
                        # If both hit in the entry bar, the chronologically FIRST target wins!
                        if sl_reachable and tp_reachable:
                            # Both are reachable! We determine which came first via path sequence.
                            if position_type == 1:
                                # Long: SL is downside, TP is upside
                                if path_first == bar_l:
                                    # Hit low first, so SL hit first
                                    sl_hit_first = True
                                else:
                                    sl_hit_first = False
                            else:
                                # Short: SL is upside, TP is downside
                                if path_first == bar_h:
                                    # Hit high first, so SL hit first
                                    sl_hit_first = True
                                else:
                                    sl_hit_first = False
                                    
                            if sl_hit_first:
                                forced_result = 'Loss'
                            else:
                                forced_result = 'Win'
                        elif sl_reachable:
                            forced_result = 'Loss'
                        elif tp_reachable:
                            forced_result = 'Win'
                        else:
                            forced_result = None

                        if forced_result == 'Loss':
                            execution_exit = (sl_price - exit_cost_points) if position_type == 1 else (sl_price + exit_cost_points)
                            pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                            balance += pnl
                            trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Loss'})
                            in_position = False
                        elif forced_result == 'Win':
                            execution_exit = (tp_price - exit_cost_points) if position_type == 1 else (tp_price + exit_cost_points)
                            pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size) if position_type == 1 else (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                            balance += pnl
                            trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long' if position_type == 1 else 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Win'})
                            in_position = False"""
    
    content = content[:start_idx] + new_block + "\n" + content[end_idx:]

    # Now fix the general exits (for non-entry bars)
    # Lines 350 to 442
    
    start_str2 = "        # Check if we need to close the current position"
    end_str2 = "        if not in_position:"
    
    start_idx2 = content.find(start_str2)
    end_idx2 = content.find(end_str2)
    
    if start_idx2 != -1 and end_idx2 != -1:
        new_block2 = """        # Check if we need to close the current position
        if in_position:
            bar_o = row['open']
            bar_h = row['high']
            bar_l = row['low']
            path_o, path_first, path_second = _bar_sequence(bar_o, bar_h, bar_l)
            
            if position_type == 1: # Long
                sl_hit = bar_l <= sl_price
                tp_hit = bar_h >= tp_price
                
                if sl_hit and tp_hit:
                    # Chronological check
                    if path_first == bar_l:
                        exit_at_sl = True # Hit SL bottom first
                    else:
                        exit_at_sl = False # Hit TP top first
                else:
                    exit_at_sl = sl_hit
                    
                if exit_at_sl:
                    execution_exit = sl_price - exit_cost_points
                    pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size)
                    balance += pnl
                    trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Loss'})
                    in_position = False
                elif tp_hit:
                    execution_exit = tp_price - exit_cost_points
                    pnl = (execution_exit - entry_price) * position_size - (commission_per_unit * position_size)
                    balance += pnl
                    trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Long', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Win'})
                    in_position = False
                    
            elif position_type == -1: # Short
                sl_hit = bar_h >= sl_price
                tp_hit = bar_l <= tp_price
                
                if sl_hit and tp_hit:
                    # Chronological check
                    if path_first == bar_h:
                        exit_at_sl = True # Hit SL top first
                    else:
                        exit_at_sl = False # Hit TP bottom first
                else:
                    exit_at_sl = sl_hit
                    
                if exit_at_sl:
                    execution_exit = sl_price + exit_cost_points
                    pnl = (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                    balance += pnl
                    trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Loss'})
                    in_position = False
                elif tp_hit:
                    execution_exit = tp_price + exit_cost_points
                    pnl = (entry_price - execution_exit) * position_size - (commission_per_unit * position_size)
                    balance += pnl
                    trades.append({'entry_time': entry_time, 'exit_time': index, 'type': 'Short', 'entry': entry_price, 'tp_price': tp_price, 'sl_price': sl_price, 'exit': execution_exit, 'pnl': pnl, 'result': 'Win'})
                    in_position = False
"""
        content = content[:start_idx2] + new_block2 + "\n" + content[end_idx2:]

    with open('backtest.py', 'w', encoding='utf-8') as f:
        f.write(content)
        
    print("Patched backtest.py fully!")

if __name__ == "__main__":
    patch_backtest()
