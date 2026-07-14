import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional
import pytz
from alpaca_client import AlpacaClient, position_symbol
from config import settings
from whatsapp_notifier import WhatsAppNotifier
from scanner import OpportunityScanner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PEAK_PRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'peak_prices_state.json')


def _now() -> str:
    """Current time in REPORT_TIMEZONE, ISO format."""
    return datetime.now(pytz.timezone(settings.REPORT_TIMEZONE)).isoformat()


class TradingManager:
    """Manages trading positions and stop loss adjustments"""

    def __init__(self):
        self.client = AlpacaClient()
        self.notifier = WhatsAppNotifier()
        self.position_entry_prices = {}  # Track entry prices
        self.position_peak_prices = self._load_peak_prices()  # Track peak prices for stop loss
        self.trade_history = []          # Track all trades for P&L analysis
        self.strategy_adjustments = []   # Track strategy changes
        self.win_streak = 0              # Current winning streak
        self.loss_streak = 0             # Current losing streak

    def _load_peak_prices(self) -> Dict:
        """Load persisted peak prices so the trailing stop survives a service restart —
        otherwise a restart silently re-seeds every peak to the current price, discarding
        any pre-restart gain the trailing stop was supposed to be protecting.
        """
        if os.path.exists(PEAK_PRICES_FILE):
            try:
                with open(PEAK_PRICES_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Failed to load peak prices state, starting fresh: {e}")
        return {}

    def _save_peak_prices(self):
        try:
            with open(PEAK_PRICES_FILE, 'w') as f:
                json.dump(self.position_peak_prices, f)
        except OSError as e:
            logger.error(f"Failed to save peak prices state: {e}")

    def check_positions(self, asset_class: Optional[str] = None) -> Dict:
        """Check all open positions and apply trading logic.

        Pass asset_class='crypto' or 'us_equity' to restrict which positions get
        evaluated. The always-on crypto job needs this scoped to 'crypto' — Alpaca
        queues a stock market order submitted outside trading hours as a next-session
        day order, so an unscoped call would let a stale overnight price trip a stock's
        5%/8% stop and realize whatever the next open's gap turns out to be, instead of
        actually capping the loss at that threshold.
        """
        try:
            positions = self.client.get_positions()
            if asset_class is not None:
                positions = [p for p in positions if p.get('asset_class') == asset_class]
            account = self.client.get_account()
            
            report = {
                'timestamp': _now(),
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
                    self._save_peak_prices()
                else:
                    if current_price > self.position_peak_prices[symbol]:
                        self.position_peak_prices[symbol] = current_price
                        self._save_peak_prices()

                # Handle stop loss adjustments
                closed = False
                if settings.ENABLE_STOP_LOSS_ADJUSTMENT:
                    closed = self._handle_stop_loss(symbol, position, pnl_pct, report)

                # Trailing stop: protects gains given back from a position's peak,
                # which the entry-anchored stop loss above can't see at all
                if not closed and settings.ENABLE_STOP_LOSS_ADJUSTMENT:
                    closed = self._handle_trailing_stop(symbol, position, report)

                # Handle re-entries
                if not closed and settings.ENABLE_REENTRY:
                    self._handle_reentry(symbol, position, pnl_pct, report)

            if report['actions_taken'] and self.notifier.enabled:
                try:
                    subject, body = self.notifier.build_position_alert_email(report)
                    self.notifier.send(subject, body)
                    logger.info('Position alert sent')
                except Exception as e:
                    logger.error(f'Failed to send position alert: {e}')

            return report
        
        except Exception as e:
            logger.error(f"Error checking positions: {e}")
            return {'error': str(e), 'timestamp': _now()}
    
    def _handle_stop_loss(self, symbol: str, position: Dict, pnl_pct: float, report: Dict) -> bool:
        """Handle stop loss adjustments at the entry-anchored threshold. Returns True if closed.

        Crypto uses its own, wider CRYPTO_STOP_LOSS_THRESHOLD instead of STOP_LOSS_THRESHOLD —
        see the backtest note next to CRYPTO_STOP_LOSS_THRESHOLD in config/settings.py for why
        the stock-tuned 5% is far too tight for crypto's ordinary volatility.
        """
        try:
            threshold = settings.CRYPTO_STOP_LOSS_THRESHOLD if position.get('asset_class') == 'crypto' else settings.STOP_LOSS_THRESHOLD
            # If position is up at or above current stop loss threshold, consider tightening stop
            if pnl_pct >= threshold:
                action = {
                    'action': 'STOP_LOSS_CANDIDATE',
                    'symbol': symbol,
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'recommendation': f'Position is up {round(pnl_pct * 100, 2)}%. Consider trailing stop or moving stop up.'
                }
                report['actions_taken'].append(action)
                logger.info(f"[{symbol}] Position up {round(pnl_pct * 100, 2)}% - Stop loss adjustment recommended")
                return False

            # If position is down at or below current stop loss threshold, close it
            elif pnl_pct <= -threshold:
                closed = False
                try:
                    self.client.close_position(symbol)
                    self._record_trade_outcome(symbol, float(position.get('unrealized_pl', 0)))
                    self.position_peak_prices.pop(symbol, None)
                    self._save_peak_prices()
                    closed = True
                    action = {
                        'action': 'STOP_LOSS_TRIGGERED',
                        'symbol': symbol,
                        'pnl_pct': round(pnl_pct * 100, 2),
                        'recommendation': f'Position was down {round(abs(pnl_pct) * 100, 2)}%. Closed automatically.'
                    }
                    logger.warning(f"[{symbol}] Position down {round(abs(pnl_pct) * 100, 2)}% - closed automatically (stop loss)")
                except Exception as close_error:
                    action = {
                        'action': 'STOP_LOSS_ALERT',
                        'symbol': symbol,
                        'pnl_pct': round(pnl_pct * 100, 2),
                        'recommendation': f'Position is down {round(abs(pnl_pct) * 100, 2)}%. Automatic close FAILED: {close_error}'
                    }
                    logger.error(f"[{symbol}] Stop loss close failed: {close_error}")
                report['actions_taken'].append(action)
                return closed

            return False

        except Exception as e:
            report['errors'].append(f"Error handling stop loss for {symbol}: {e}")
            logger.error(f"Error handling stop loss for {symbol}: {e}")
            return False

    def _handle_trailing_stop(self, symbol: str, position: Dict, report: Dict) -> bool:
        """Close a position if it has pulled back TRAILING_STOP_THRESHOLD from its peak price.

        Separate from _handle_reentry's pullback-from-peak check below, which is an
        advisory add-to-position suggestion on REENTRY_THRESHOLD, not a sell — this
        is the downside-protection counterpart, using TRAILING_STOP_THRESHOLD (wider than
        the entry-anchored STOP_LOSS_THRESHOLD) since the entry-anchored stop loss above
        can't see gains a position has given back, and a peak-relative stop needs more
        room than an entry-relative one to avoid closing on ordinary volatility.

        Crypto uses its own, wider CRYPTO_TRAILING_STOP_THRESHOLD instead of
        TRAILING_STOP_THRESHOLD — see the backtest note next to CRYPTO_TRAILING_STOP_THRESHOLD
        in config/settings.py for why the stock-tuned 8% is far too tight for crypto.
        Returns True if the position was closed.
        """
        try:
            threshold = settings.CRYPTO_TRAILING_STOP_THRESHOLD if position.get('asset_class') == 'crypto' else settings.TRAILING_STOP_THRESHOLD
            peak_price = self.position_peak_prices.get(symbol, 0)
            current_price = float(position.get('current_price', 0))
            if peak_price <= 0:
                return False

            pullback_pct = (peak_price - current_price) / peak_price
            if pullback_pct < threshold:
                return False

            try:
                self.client.close_position(symbol)
                self._record_trade_outcome(symbol, float(position.get('unrealized_pl', 0)))
                self.position_peak_prices.pop(symbol, None)
                self._save_peak_prices()
                action = {
                    'action': 'TRAILING_STOP_TRIGGERED',
                    'symbol': symbol,
                    'pullback_pct': round(pullback_pct * 100, 2),
                    'peak_price': round(peak_price, 2),
                    'recommendation': f'Pulled back {round(pullback_pct * 100, 2)}% from peak (${round(peak_price, 2)}). Closed automatically.'
                }
                logger.warning(f"[{symbol}] Pulled back {round(pullback_pct * 100, 2)}% from peak ${round(peak_price, 2)} - closed automatically (trailing stop)")
                report['actions_taken'].append(action)
                return True
            except Exception as close_error:
                action = {
                    'action': 'TRAILING_STOP_ALERT',
                    'symbol': symbol,
                    'pullback_pct': round(pullback_pct * 100, 2),
                    'recommendation': f'Pulled back {round(pullback_pct * 100, 2)}% from peak. Automatic close FAILED: {close_error}'
                }
                logger.error(f"[{symbol}] Trailing stop close failed: {close_error}")
                report['actions_taken'].append(action)
                return False

        except Exception as e:
            report['errors'].append(f"Error handling trailing stop for {symbol}: {e}")
            logger.error(f"Error handling trailing stop for {symbol}: {e}")
            return False
    
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
                'timestamp': _now(),
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
    
    def _record_trade_outcome(self, symbol: str, pnl: float):
        """Update win/loss streak from a single closed trade's realized P&L.

        Streaks are driven by actual closed trades, not by the concurrent
        unrealized P&L direction of whatever happens to be open — two
        correlated positions dipping together for a few hourly checks isn't
        a losing streak, it's one market move.
        """
        if pnl > 0:
            self.win_streak += 1
            self.loss_streak = 0
        elif pnl < 0:
            self.loss_streak += 1
            self.win_streak = 0
        self.trade_history.append({'symbol': symbol, 'pnl': pnl, 'timestamp': _now()})

    def analyze_performance(self) -> Dict:
        """Snapshot of current unrealized P&L plus the closed-trade win/loss streak"""
        try:
            positions = self.client.get_positions()
            account = self.client.get_account()

            total_pnl = 0
            winning_positions = 0
            losing_positions = 0

            for position in positions:
                pnl = float(position.get('unrealized_pl', 0))
                total_pnl += pnl

                if pnl > 0:
                    winning_positions += 1
                elif pnl < 0:
                    losing_positions += 1

            return {
                'total_pnl': total_pnl,
                'winning_positions': winning_positions,
                'losing_positions': losing_positions,
                'win_streak': self.win_streak,
                'loss_streak': self.loss_streak,
                'current_equity': account.get('equity'),
                'account_return': ((float(account.get('equity', settings.INITIAL_EQUITY)) - settings.INITIAL_EQUITY) / settings.INITIAL_EQUITY) * 100
            }
        
        except Exception as e:
            logger.error(f"Error analyzing performance: {e}")
            return {'error': str(e)}

    def scan_and_execute(self, watchlist=None, position_size_usd=None, max_positions=None) -> Dict:
        """Scan a watchlist for opportunities and execute buy/sell orders.

        Defaults to the stock watchlist/sizing; pass settings.CRYPTO_WATCHLIST etc.
        to run the same logic against crypto instead.
        """
        watchlist = watchlist if watchlist is not None else settings.WATCHLIST
        position_size_usd = position_size_usd if position_size_usd is not None else settings.POSITION_SIZE_USD
        max_positions = max_positions if max_positions is not None else settings.MAX_POSITIONS

        scanner = OpportunityScanner(self.client)
        signals = scanner.scan(watchlist)

        try:
            all_positions = self.client.get_positions()
            account = self.client.get_account()
        except Exception as e:
            return {'timestamp': _now(), 'error': str(e), 'signals': signals, 'executed': [], 'errors': [str(e)]}

        # Crypto and stock orders/positions use different symbol formats (BTC/USD vs
        # BTCUSD) and should be capped independently, so scope everything below to the
        # asset class this watchlist actually represents.
        is_crypto = any('/' in s for s in watchlist)
        asset_class = 'crypto' if is_crypto else 'us_equity'
        positions = [p for p in all_positions if p.get('asset_class') == asset_class]

        current_symbols = {p['symbol'] for p in positions}
        buying_power = float(account.get('buying_power', 0))
        executed = []
        errors = []

        for sig in signals:
            symbol = sig['symbol']
            signal = sig['signal']
            price = float(sig.get('price', 0))
            pos_symbol = position_symbol(symbol)

            if signal == 'buy':
                if pos_symbol in current_symbols:
                    continue
                pending_buys = sum(1 for e in executed if e['side'] == 'buy')
                if len(positions) + pending_buys >= max_positions:
                    logger.info(f"[SCANNER] Skipping {symbol} buy — max positions ({max_positions}) reached")
                    continue
                if price <= 0 or buying_power < position_size_usd:
                    logger.info(f"[SCANNER] Skipping {symbol} buy — insufficient buying power (${buying_power:.2f})")
                    continue
                notional = round(position_size_usd, 2)
                client_order_id = f"scan-buy-{symbol.replace('/', '')}-{int(datetime.now().timestamp())}"
                try:
                    order = self.client.create_order(symbol, side='buy', notional=notional,
                                                       client_order_id=client_order_id)
                    qty = round(notional / price, 4)
                    buying_power -= notional
                    executed.append({'side': 'buy', 'symbol': symbol, 'qty': qty, 'price': price, 'reason': sig['reason'], 'order_id': order.get('id')})
                    logger.info(f"[SCANNER] BUY ~${notional:.2f} ({qty} sh) {symbol} @ ~${price:.2f} — {sig['reason']}")
                except Exception as e:
                    errors.append(f"Buy {symbol}: {e}")
                    logger.error(f"[SCANNER] Failed to buy {symbol}: {e}")

            elif signal == 'sell' and pos_symbol in current_symbols:
                try:
                    position = next((p for p in positions if p['symbol'] == pos_symbol), None)
                    self.client.close_position(pos_symbol)
                    executed.append({'side': 'sell', 'symbol': symbol, 'reason': sig['reason']})
                    logger.info(f"[SCANNER] SELL {symbol} — {sig['reason']}")
                    if position is not None:
                        self._record_trade_outcome(pos_symbol, float(position.get('unrealized_pl', 0)))
                    self.position_peak_prices.pop(pos_symbol, None)
                    self._save_peak_prices()
                except Exception as e:
                    errors.append(f"Sell {symbol}: {e}")
                    logger.error(f"[SCANNER] Failed to sell {symbol}: {e}")

        result = {
            'timestamp': _now(),
            'watchlist': watchlist,
            'signals': signals,
            'executed': executed,
            'errors': errors,
        }

        if executed and self.notifier.enabled:
            try:
                subject, body = self.notifier.build_trade_execution_email(result)
                self.notifier.send(subject, body)
                logger.info('Trade execution email sent')
            except Exception as e:
                logger.error(f'Failed to send trade email: {e}')

        return result

    def build_daily_report(self) -> Dict:
        """Build a combined account status + performance summary for the daily report email."""
        return {
            'timestamp': _now(),
            'status': self.get_trading_status(),
            'performance': self.analyze_performance()
        }
