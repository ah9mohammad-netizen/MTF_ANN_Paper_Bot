//+------------------------------------------------------------------+
//|                                     XAUUSD_Layered_Scalper.mq5   |
//|                   Copyright 2026, Gold-Scalp Quantitative Team   |
//|                             https://github.com/ah9mohammad-netizen|
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Gold-Scalp Quantitative Team"
#property link      "https://github.com/ah9mohammad-netizen/Gold-Scalp"
#property version   "1.00"
#property description "Layered Decision-Making Scalping EA for XAUUSD (MT5)"
#property strict

//--- Input Parameters
input group "=== Risk & Position Sizing ==="
input double   InpRiskPercent       = 1.0;    // Account Risk per Trade (%)
input double   InpSlAtrMultiplier   = 1.5;    // Stop Loss ATR Multiplier
input double   InpTpRrRatio         = 2.0;    // Take Profit R:R Multiplier
input int      InpMaxSpreadPoints   = 40;     // Max Allowable Spread (Points, 40 = $0.40)

input group "=== Technical & Volatility Parameters ==="
input int      InpEmaPeriod         = 200;    // Macro EMA Period (M15/H1)
input int      InpAtrPeriod         = 14;     // ATR Period (M5)
input double   InpMinAtrDollars     = 1.20;   // Minimum ATR Volatility ($/oz)
input int      InpRsiPeriod         = 14;     // RSI Period
input double   InpRsiOverbought     = 72.0;   // RSI Overbought Threshold
input double   InpRsiOversold       = 28.0;   // RSI Oversold Threshold

input group "=== Session Windows (UTC Hours) ==="
input int      InpLondonStartHour   = 7;      // London Open Start Hour
input int      InpLondonEndHour     = 10;     // London Open End Hour
input int      InpNyStartHour       = 12;     // New York Overlap Start Hour
input int      InpNyEndHour         = 16;     // New York Overlap End Hour

//--- Global Handles
int      handle_ema;
int      handle_atr;
int      handle_rsi;
CTrade   trade;

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Initialize Indicator Handles
   handle_ema = iMA(_Symbol, PERIOD_CURRENT, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   handle_atr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   handle_rsi = iRSI(_Symbol, PERIOD_CURRENT, InpRsiPeriod, PRICE_CLOSE);

   if(handle_ema == INVALID_HANDLE || handle_atr == INVALID_HANDLE || handle_rsi == INVALID_HANDLE)
     {
      Print("Error initializing technical indicators for XAUUSD Layered Scalper!");
      return(INIT_FAILED);
     }
     
   trade.SetExpertMagicNumber(20260721);
   trade.SetDeviationInPoints(10); // Allow 10 points slippage
   Print("XAUUSD Layered Scalper EA successfully initialized.");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handle_ema);
   IndicatorRelease(handle_atr);
   IndicatorRelease(handle_rsi);
   Print("XAUUSD Layered Scalper EA deinitialized.");
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   // Check open positions - only trade one position at a time per symbol
   if(PositionsTotal() > 0)
     {
      ManageTrailingStop();
      return;
     }

   // --------------------------------------------------------
   // LAYER 1: REGIME, SESSION & SPREAD FILTER
   // --------------------------------------------------------
   MqlDateTime dt;
   TimeCurrent(dt);
   bool inLondon = (dt.hour >= InpLondonStartHour && dt.hour < InpLondonEndHour);
   bool inNY     = (dt.hour >= InpNyStartHour && dt.hour < InpNyEndHour);
   if(!inLondon && !inNY) return; // Outside active liquidity windows

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints) return; // Spread too wide (news or rollover)

   // --------------------------------------------------------
   // LAYER 2 & 3: STRUCTURAL & MOMENTUM CONFIRMATION
   // --------------------------------------------------------
   double ema[], atr[], rsi[], close_arr[];
   ArraySetAsSeries(ema, true);
   ArraySetAsSeries(atr, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(close_arr, true);

   if(CopyBuffer(handle_ema, 0, 1, 2, ema) <= 0) return;
   if(CopyBuffer(handle_atr, 0, 1, 2, atr) <= 0) return;
   if(CopyBuffer(handle_rsi, 0, 1, 2, rsi) <= 0) return;
   if(CopyClose(_Symbol, PERIOD_CURRENT, 1, 2, close_arr) <= 0) return;

   double current_close = close_arr[0];
   double current_ema   = ema[0];
   double current_atr   = atr[0];
   double current_rsi   = rsi[0];

   if(current_atr < InpMinAtrDollars) return; // Volatility too low to scalp

   bool long_signal  = (current_close > current_ema) && (current_rsi < InpRsiOverbought) && (current_rsi > 52.0);
   bool short_signal = (current_close < current_ema) && (current_rsi > InpRsiOversold)   && (current_rsi < 48.0);

   // --------------------------------------------------------
   // LAYER 4: DYNAMIC RISK ENGINE & EXECUTION
   // --------------------------------------------------------
   if(long_signal)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl_dist = current_atr * InpSlAtrMultiplier;
      double sl = ask - sl_dist;
      double tp = ask + (sl_dist * InpTpRrRatio);
      double lots = CalculateLots(sl_dist);

      if(lots > 0)
        {
         trade.Buy(lots, _Symbol, ask, sl, tp, "XAU_Layered_Long");
         PrintFormat("Long Executed | Lots: %.2f | Ask: %.2f | SL: %.2f | TP: %.2f", lots, ask, sl, tp);
        }
     }
   else if(short_signal)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl_dist = current_atr * InpSlAtrMultiplier;
      double sl = bid + sl_dist;
      double tp = bid - (sl_dist * InpTpRrRatio);
      double lots = CalculateLots(sl_dist);

      if(lots > 0)
        {
         trade.Sell(lots, _Symbol, bid, sl, tp, "XAU_Layered_Short");
         PrintFormat("Short Executed | Lots: %.2f | Bid: %.2f | SL: %.2f | TP: %.2f", lots, bid, sl, tp);
        }
     }
  }

//+------------------------------------------------------------------+
//| Dynamic Lot Size Calculation Based on Account Equity & SL Distance|
//+------------------------------------------------------------------+
double CalculateLots(double sl_distance_dollars)
  {
   if(sl_distance_dollars <= 0) return 0.0;
   
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_amount = equity * (InpRiskPercent / 100.0);
   
   // In XAUUSD, 1 standard lot = 100 troy ounces.
   // Dollar risk = Lots * ContractSize * SL_Distance
   double contract_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(contract_size <= 0) contract_size = 100.0;
   
   double raw_lots = risk_amount / (contract_size * sl_distance_dollars);
   
   double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   
   double lots = MathFloor(raw_lots / step_lot) * step_lot;
   if(lots < min_lot) lots = min_lot;
   if(lots > max_lot) lots = max_lot;
   
   return lots;
  }

//+------------------------------------------------------------------+
//| Trailing Stop Logic (Moves SL to Breakeven at 1.0R Profit)       |
//+------------------------------------------------------------------+
void ManageTrailingStop()
  {
   for(int i=PositionsTotal()-1; i>=0; i--)
     {
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == 20260721)
        {
         double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
         double current_sl = PositionGetDouble(POSITION_SL);
         long pos_type     = PositionGetInteger(POSITION_TYPE);
         
         double atr_buffer[];
         ArraySetAsSeries(atr_buffer, true);
         if(CopyBuffer(handle_atr, 0, 1, 1, atr_buffer) <= 0) continue;
         double atr_val = atr_buffer[0];
         
         if(pos_type == POSITION_TYPE_BUY)
           {
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(bid - open_price >= atr_val && current_sl < open_price)
              {
               trade.PositionModify(PositionGetInteger(POSITION_TICKET), open_price + 0.10, PositionGetDouble(POSITION_TP));
               Print("Long Trailing Stop moved to Breakeven + $0.10");
              }
           }
         else if(pos_type == POSITION_TYPE_SELL)
           {
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            if(open_price - ask >= atr_val && (current_sl > open_price || current_sl == 0))
              {
               trade.PositionModify(PositionGetInteger(POSITION_TICKET), open_price - 0.10, PositionGetDouble(POSITION_TP));
               Print("Short Trailing Stop moved to Breakeven - $0.10");
              }
           }
        }
     }
  }
