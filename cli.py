import logging
from scheduler import get_scheduler
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TradingCLI:
    """Interactive CLI for trading bot management"""
    
    def __init__(self):
        self.scheduler = get_scheduler()
        self.trading_manager = self.scheduler.trading_manager
        self.commands = {
            '/schedule': self.cmd_schedule,
            '/status': self.cmd_status,
            '/check': self.cmd_check,
            '/history': self.cmd_history,
            '/config': self.cmd_config,
            '/order': self.cmd_order,
            '/positions': self.cmd_positions,
            '/help': self.cmd_help,
            '/exit': self.cmd_exit,
        }
    
    def cmd_schedule(self, args: list):
        """
        /schedule <start|stop|status>
        
        Manage the market hours scheduler
        """
        if not args:
            self._print_help('schedule')
            return
        
        action = args[0].lower()
        
        if action == 'start':
            if not self.scheduler.is_running:
                self.scheduler.start()
                print("✓ Scheduler started. Position checks will run every", 
                      settings.CHECK_INTERVAL_MINUTES, "minutes during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)")
            else:
                print("⚠ Scheduler is already running")
        
        elif action == 'stop':
            if self.scheduler.is_running:
                self.scheduler.stop()
                print("✓ Scheduler stopped")
            else:
                print("⚠ Scheduler is not running")
        
        elif action == 'status':
            status = self.scheduler.get_status()
            print(f"\nScheduler Status:")
            print(f"  Running: {'Yes' if status['running'] else 'No'}")
            print(f"  Active jobs: {status['jobs']}")
            print(f"  Last check: {status.get('last_check', {}).get('timestamp', 'Never')}")
            print(f"  History size: {status['check_history_size']} checks")
        
        else:
            print(f"Unknown action: {action}. Use: start, stop, status")
    
    def cmd_status(self, args: list):
        """
        /status
        
        Show current account and market status
        """
        try:
            status = self.trading_manager.get_trading_status()
            
            if 'error' in status:
                print(f"✗ Error: {status['error']}")
                return
            
            print(f"\nAccount Status:")
            print(f"  Status: {status.get('account_status')}")
            print(f"  Equity: ${status.get('equity', 'N/A')}")
            print(f"  Buying Power: ${status.get('buying_power', 'N/A')}")
            print(f"  Trading Blocked: {status.get('trading_blocked')}")
            print(f"  Open Positions: {status.get('open_positions')}")
            
            if status.get('positions'):
                print(f"\n  Positions:")
                for pos in status['positions']:
                    symbol = pos.get('symbol')
                    qty = pos.get('qty')
                    current = float(pos.get('current_price', 0))
                    entry = float(pos.get('avg_entry_price', 0))
                    pnl = (current - entry) * float(qty)
                    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                    print(f"    {symbol}: {qty} shares @ ${current} (Entry: ${entry}, P&L: ${pnl:.2f} / {pnl_pct:.2f}%)")
        
        except Exception as e:
            print(f"✗ Error: {e}")
    
    def cmd_check(self, args: list):
        """
        /check
        
        Manually trigger a position check now
        """
        print("Running manual position check...")
        report = self.trading_manager.check_positions()
        
        print(f"\nPosition Check Report ({report.get('timestamp')}):")
        print(f"  Account Equity: ${report.get('account_equity', 'N/A')}")
        print(f"  Buying Power: ${report.get('buying_power', 'N/A')}")
        print(f"  Positions Checked: {report.get('positions_checked')}")
        
        actions = report.get('actions_taken', [])
        if actions:
            print(f"\n  Actions/Recommendations ({len(actions)}):")
            for action in actions:
                print(f"    - {action.get('action')}: {action.get('symbol')}")
                print(f"      → {action.get('recommendation')}")
        else:
            print("  No actions recommended")
        
        errors = report.get('errors', [])
        if errors:
            print(f"\n  Errors:")
            for error in errors:
                print(f"    - {error}")
    
    def cmd_history(self, args: list):
        """
        /history [limit]
        
        Show last N position checks (default: 10)
        """
        limit = int(args[0]) if args else 10
        history = self.scheduler.get_history(limit)
        
        print(f"\nLast {len(history)} Position Checks:")
        for i, check in enumerate(history, 1):
            timestamp = check.get('timestamp', 'Unknown')
            actions = len(check.get('actions_taken', []))
            errors = len(check.get('errors', []))
            print(f"  {i}. [{timestamp}] {actions} action(s), {errors} error(s)")
    
    def cmd_config(self, args: list):
        """
        /config [set|show]
        
        Show or modify configuration
        """
        if not args or args[0] == 'show':
            print("\nCurrent Configuration:")
            print(f"  CHECK_INTERVAL_MINUTES: {settings.CHECK_INTERVAL_MINUTES}")
            print(f"  STOP_LOSS_THRESHOLD: {settings.STOP_LOSS_THRESHOLD * 100}%")
            print(f"  REENTRY_THRESHOLD: {settings.REENTRY_THRESHOLD * 100}%")
            print(f"  MARKET_OPEN: {settings.MARKET_OPEN_HOUR}:{settings.MARKET_OPEN_MINUTE:02d} ET")
            print(f"  MARKET_CLOSE: {settings.MARKET_CLOSE_HOUR}:{settings.MARKET_CLOSE_MINUTE:02d} ET")
            print(f"  ENABLE_STOP_LOSS_ADJUSTMENT: {settings.ENABLE_STOP_LOSS_ADJUSTMENT}")
            print(f"  ENABLE_REENTRY: {settings.ENABLE_REENTRY}")
        
        elif args[0] == 'set':
            if len(args) < 3:
                print("Usage: /config set <parameter> <value>")
                return
            
            param, value = args[1], args[2]
            try:
                if param == 'stop_loss':
                    settings.STOP_LOSS_THRESHOLD = float(value) / 100
                    print(f"✓ Stop loss threshold set to {value}%")
                elif param == 'reentry':
                    settings.REENTRY_THRESHOLD = float(value) / 100
                    print(f"✓ Re-entry threshold set to {value}%")
                elif param == 'interval':
                    settings.CHECK_INTERVAL_MINUTES = int(value)
                    print(f"✓ Check interval set to {value} minutes")
                else:
                    print(f"Unknown parameter: {param}")
            except Exception as e:
                print(f"✗ Error setting configuration: {e}")
    
    def cmd_order(self, args: list):
        """
        /order <symbol> <qty> <buy|sell> [market|limit] [limit_price]
        
        Place a trade order
        Example: /order AAPL 1 buy market
        """
        if len(args) < 3:
            print("Usage: /order <symbol> <qty> <buy|sell> [market|limit] [limit_price]")
            return
        
        symbol = args[0].upper()
        qty = float(args[1])
        side = args[2].lower()
        order_type = args[3].lower() if len(args) > 3 else 'market'
        
        if side not in ['buy', 'sell']:
            print("Side must be 'buy' or 'sell'")
            return
        
        try:
            print(f"Placing order: {side.upper()} {qty} {symbol}...")
            result = self.trading_manager.execute_order(symbol, qty, side, order_type)
            
            if result['success']:
                order = result['order']
                print(f"✓ Order placed successfully!")
                print(f"  Order ID: {order.get('id')}")
                print(f"  Symbol: {order.get('symbol')}")
                print(f"  Side: {order.get('side').upper()}")
                print(f"  Qty: {order.get('qty')}")
                print(f"  Status: {order.get('status')}")
            else:
                print(f"✗ Order failed: {result.get('error')}")
        
        except Exception as e:
            print(f"✗ Error: {e}")
    
    def cmd_positions(self, args: list):
        """
        /positions
        
        Show all open positions in detail
        """
        try:
            positions = self.trading_manager.client.get_positions()
            
            if not positions:
                print("No open positions")
                return
            
            print(f"\nOpen Positions ({len(positions)}):")
            for pos in positions:
                symbol = pos.get('symbol')
                qty = float(pos.get('qty', 0))
                current = float(pos.get('current_price', 0))
                entry = float(pos.get('avg_entry_price', 0))
                market_value = float(pos.get('market_value', 0))
                pnl = float(pos.get('unrealized_pl', 0))
                pnl_pct = float(pos.get('unrealized_plpc', 0)) * 100
                
                print(f"\n  {symbol}:")
                print(f"    Qty: {qty}")
                print(f"    Entry Price: ${entry:.2f}")
                print(f"    Current Price: ${current:.2f}")
                print(f"    Market Value: ${market_value:.2f}")
                print(f"    P&L: ${pnl:.2f} ({pnl_pct:.2f}%)")
        
        except Exception as e:
            print(f"✗ Error: {e}")
    
    def cmd_help(self, args: list):
        """Show help for all commands"""
        print("\n" + "=" * 60)
        print("Trading Bot - Command Reference")
        print("=" * 60)
        
        for cmd, func in self.commands.items():
            if cmd == '/exit':
                continue
            doc = func.__doc__ or "No description"
            print(f"\n{cmd}")
            print(doc)
        
        print("\n" + "=" * 60)
    
    def cmd_exit(self, args: list):
        """Exit the application"""
        print("Exiting...")
        if self.scheduler.is_running:
            self.scheduler.stop()
        return False
    
    def _print_help(self, command: str):
        """Print help for a specific command"""
        if command in self.commands:
            print(self.commands[command].__doc__)
    
    def run(self):
        """Start the interactive CLI"""
        print("=" * 60)
        print("Alpaca Trading Bot - Market Hours Scheduler")
        print("=" * 60)
        print("\nType '/help' for available commands")
        print("Type '/schedule start' to begin monitoring\n")
        
        while True:
            try:
                user_input = input("> ").strip()
                
                if not user_input:
                    continue
                
                parts = user_input.split()
                command = parts[0].lower()
                args = parts[1:]
                
                if command not in self.commands:
                    print(f"Unknown command: {command}. Type '/help' for available commands.")
                    continue
                
                result = self.commands[command](args)
                
                # Check if command returned False (exit signal)
                if result is False:
                    break
            
            except KeyboardInterrupt:
                print("\n\nInterrupted. Type '/exit' to quit.")
            except Exception as e:
                logger.error(f"Error: {e}")
                print(f"✗ Error: {e}")
