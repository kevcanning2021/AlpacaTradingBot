import logging
from datetime import datetime
from typing import Dict
from alpaca_client import AlpacaClient
from config import settings
from email_notifier import EmailNotifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TradingManager:
    """Manages trading positions and stop loss adjustments"""
    
    def __init__(self):
        self.client = AlpacaClient()
        self.notifier = EmailNotifier()
        self.position_entry_prices = {}  # Track entry prices
        self.position_peak_prices = {}   # Track peak prices for stop loss
        self.trade_history = []          # Track all trades for P&L analysis
        self.strategy_adjustments = []   # Track strategy changes
        self.win_streak = 0              # Current winning streak
        self.loss_streak = 0             # Current losing streak
    
    def check_positions(self) -> Dict:
        """Check all open positions and apply trading logic"""
        try:
            positions = self.client.get_positions()
            account = self.client.get_account()
            
            report = {
                'timestamp': datetime.now().isoformat(),
                'account_equity': account.get('equity'),
                'buying_power': account.get('buying_power'),
                'positions_checked': len(positions),
                'actions_taken': [],
                'errors': []
            }
            
            for position in positions:
                symbol = position.get('symbol')
                qty = float(position.get('qty', 0))
                current_price = float(position.get('current_price', 0))
                avg_entry_price = float(position.get('avg_entry_price', 0))
                
                logger.info(f"Checking {symbol}: {qty} shares @ ${current_price} (Entry: ${avg_entry_price})")
                
                # Calculate gain/loss percentage
                pnl_pct = (current_price - avg_entry_price) / avg_entry_price if avg_entry_price > 0 else 0
                
                # Update peak price tracking
                if symbol not in self.position_peak_prices:
                    self.position_peak_prices[symbol] = current_price
                else:
                    if current_price > self.position_peak_prices[symbol]:
                        self.position_peak_prices[symbol] = current_price
                
                # Handle stop loss adjustments
                if settings.ENABLE_STOP_LOSS_ADJUSTMENT:
                    self._handle_stop_loss(symbol, position, pnl_pct, report)
                
                # Handle re-entries
                if settings.ENABLE_REENTRY:
                    self._handle_reentry(symbol, position, pnl_pct, report)
            
            return report
        
        except Exception as e:
            logger.error(f"Error checking positions: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def _handle_stop_loss(self, symbol: str, position: Dict, pnl_pct: float, report: Dict):
        """Handle stop loss adjustments at 5% threshold"""
        try:
            # If position is up at or above current stop loss threshold, consider tightening stop
            if pnl_pct >= settings.STOP_LOSS_THRESHOLD:
                action = {
                    'action': 'STOP_LOSS_CANDIDATE',
                    'symbol': symbol,
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'recommendation': f'Position is up {round(pnl_pct * 100, 2)}%. Consider trailing stop or moving stop up.'
                }
                report['actions_taken'].append(action)
                logger.info(f"[{symbol}] Position up {round(pnl_pct * 100, 2)}% - Stop loss adjustment recommended")
            
            # If position is down at or below current stop loss threshold, alert
            elif pnl_pct <= -settings.STOP_LOSS_THRESHOLD:
                action = {
                    'action': 'STOP_LOSS_ALERT',
                    'symbol': symbol,
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'recommendation': f'Position is down {round(abs(pnl_pct) * 100, 2)}%. Review stop loss level.'
                }
                report['actions_taken'].append(action)
                logger.warning(f"[{symbol}] Position down {round(abs(pnl_pct) * 100, 2)}% - Stop loss review recommended")
        
        except Exception as e:
            report['errors'].append(f"Error handling stop loss for {symbol}: {e}")
            logger.error(f"Error handling stop loss for {symbol}: {e}")
    
    def _handle_reentry(self, symbol: str, position: Dict, pnl_pct: float, report: Dict):
        """Handle re-entry logic at current pullback threshold"""
        try:
            peak_price = self.position_peak_prices.get(symbol, 0)
            current_price = float(position.get('current_price', 0))
            
            if peak_price > 0:
                pullback_pct = (peak_price - current_price) / peak_price
                
                # If we've had a pullback at or above the current re-entry threshold, suggest re-entry
                if pullback_pct >= settings.REENTRY_THRESHOLD:
                    action = {
                        'action': 'REENTRY_CANDIDATE',
                        'symbol': symbol,
                        'pullback_pct': round(pullback_pct * 100, 2),
                        'peak_price': round(peak_price, 2),
                        'current_price': round(current_price, 2),
                        'recommendation': f'Position pulled back {round(pullback_pct * 100, 2)}% from peak. Consider re-entry.'
                    }
                    report['actions_taken'].append(action)
                    logger.info(f"[{symbol}] Pullback of {round(pullback_pct * 100, 2)}% detected - Re-entry candidate")
        
        except Exception as e:
            report['errors'].append(f"Error handling re-entry for {symbol}: {e}")
            logger.error(f"Error handling re-entry for {symbol}: {e}")
    
    def execute_order(self, symbol: str, qty: float, side: str, order_type: str = 'market') -> Dict:
        """Execute a trade order"""
        try:
            order = self.client.create_order(symbol, qty, side, order_type)
            logger.info(f"Order executed: {side} {qty} {symbol} - Order ID: {order.get('id')}")
            return {'success': True, 'order': order}
        except Exception as e:
            logger.error(f"Failed to execute order: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_trading_status(self) -> Dict:
        """Get current trading status"""
        try:
            account = self.client.get_account()
            positions = self.client.get_positions()
            
            return {
                'account_status': account.get('status'),
                'trading_blocked': account.get('trading_blocked'),
                'equity': account.get('equity'),
                'buying_power': account.get('buying_power'),
                'open_positions': len(positions),
                'positions': positions
            }
        except Exception as e:
            return {'error': str(e)}
    
    def adjust_strategy(self) -> Dict:
        """Dynamically adjust strategy parameters based on performance"""
        try:
            account = self.client.get_account()
            current_equity = float(account.get('equity', settings.INITIAL_EQUITY))

            adjustments = {
                'timestamp': datetime.now().isoformat(),
                'previous_stop_loss': settings.STOP_LOSS_THRESHOLD,
                'previous_reentry': settings.REENTRY_THRESHOLD,
                'changes_made': [],
                'rationale': []
            }
            
            # Calculate account performance
            equity_change_pct = (current_equity - settings.INITIAL_EQUITY) / settings.INITIAL_EQUITY
            
            # Strategy 1: Tighten stops on winning streaks
            if self.win_streak >= 3:
                new_stop_loss = max(0.02, settings.STOP_LOSS_THRESHOLD - 0.01)
                adjustments['changes_made'].append(f'Stop loss: {settings.STOP_LOSS_THRESHOLD*100}% → {new_stop_loss*100}%')
                adjustments['rationale'].append(f'Win streak of {self.win_streak} - tighten stops')
                settings.STOP_LOSS_THRESHOLD = new_stop_loss
                logger.info(f"[STRATEGY] Win streak {self.win_streak}: Tightening stops to {new_stop_loss*100}%")
            
            # Strategy 2: Loosen stops on losing streaks
            elif self.loss_streak >= 3:
                new_stop_loss = min(0.10, settings.STOP_LOSS_THRESHOLD + 0.02)
                adjustments['changes_made'].append(f'Stop loss: {settings.STOP_LOSS_THRESHOLD*100}% → {new_stop_loss*100}%')
                adjustments['rationale'].append(f'Loss streak of {self.loss_streak} - loosen stops for breathing room')
                settings.STOP_LOSS_THRESHOLD = new_stop_loss
                logger.info(f"[STRATEGY] Loss streak {self.loss_streak}: Loosening stops to {new_stop_loss*100}%")
            
            # Strategy 3: Adjust reentry based on volatility
            if equity_change_pct > 0.05:  # Up 5%+
                new_reentry = max(0.03, settings.REENTRY_THRESHOLD - 0.01)
                adjustments['changes_made'].append(f'Re-entry: {settings.REENTRY_THRESHOLD*100}% → {new_reentry*100}%')
                adjustments['rationale'].append('Strong uptrend - reduce re-entry pullback requirement')
                settings.REENTRY_THRESHOLD = new_reentry
                logger.info(f"[STRATEGY] Account up {equity_change_pct*100:.2f}%: Tightening re-entry to {new_reentry*100}%")
            
            elif equity_change_pct < -0.05:  # Down 5%+
                new_reentry = min(0.08, settings.REENTRY_THRESHOLD + 0.02)
                adjustments['changes_made'].append(f'Re-entry: {settings.REENTRY_THRESHOLD*100}% → {new_reentry*100}%')
                adjustments['rationale'].append('Drawdown detected - increase re-entry pullback for safety')
                settings.REENTRY_THRESHOLD = new_reentry
                logger.info(f"[STRATEGY] Account down {abs(equity_change_pct)*100:.2f}%: Loosening re-entry to {new_reentry*100}%")
            
            self.strategy_adjustments.append(adjustments)

            if adjustments.get('changes_made') and self.notifier.enabled:
                try:
                    subject, body = self.notifier.build_strategy_change_email(adjustments)
                    self.notifier.send(subject, body)
                    logger.info('Strategy change email sent successfully')
                except Exception as email_error:
                    logger.error(f'Failed to send strategy change email: {email_error}')
                    adjustments['email_error'] = str(email_error)
            
            return adjustments
        
        except Exception as e:
            logger.error(f"Error adjusting strategy: {e}")
            return {'error': str(e)}
    
    def analyze_performance(self) -> Dict:
        """Analyze trading performance and streaks"""
        try:
            positions = self.client.get_positions()
            account = self.client.get_account()
            
            total_pnl = 0
            winning_trades = 0
            losing_trades = 0
            
            for position in positions:
                pnl = float(position.get('unrealized_pl', 0))
                total_pnl += pnl
                
                if pnl > 0:
                    winning_trades += 1
                elif pnl < 0:
                    losing_trades += 1
            
            # Update streaks (simplified - based on current session)
            if winning_trades > losing_trades and losing_trades == 0:
                self.win_streak += 1
                self.loss_streak = 0
            elif losing_trades > winning_trades and winning_trades == 0:
                self.loss_streak += 1
                self.win_streak = 0
            else:
                self.win_streak = 0
                self.loss_streak = 0
            
            return {
                'total_pnl': total_pnl,
                'winning_positions': winning_trades,
                'losing_positions': losing_trades,
                'win_streak': self.win_streak,
                'loss_streak': self.loss_streak,
                'current_equity': account.get('equity'),
                'account_return': ((float(account.get('equity', settings.INITIAL_EQUITY)) - settings.INITIAL_EQUITY) / settings.INITIAL_EQUITY) * 100
            }
        
        except Exception as e:
            logger.error(f"Error analyzing performance: {e}")
            return {'error': str(e)}
