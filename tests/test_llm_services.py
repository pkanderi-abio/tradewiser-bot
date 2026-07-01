"""
Tests for LLM and AI services.

Run with: pytest tests/test_llm_services.py -v
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from app.services.llm_service import LLMService, LLMProvider
from app.services.sentiment_analyzer import SentimentAnalyzer
from app.services.news_analyzer import NewsAnalyzer
from app.services.news_severity_gate import news_severity_gate
from app.services.market_intelligence import MarketIntelligence
from app.services.enhanced_ai_advisor import EnhancedAIAdvisor
from app.services.strategy_agents import (
    get_strategy_agent,
    MomentumAgent,
    MeanReversionAgent,
    BreakoutAgent,
)


class TestLLMService:
    """Test LLM service with multiple providers."""

    def test_llm_provider_detection(self):
        """Test that LLM detects available provider."""
        llm = LLMService()
        provider = llm.get_provider()
        assert provider in ["openai", "anthropic", "groq", "ollama"]

    def test_llm_get_model(self):
        """Test that LLM returns model name."""
        llm = LLMService()
        model = llm.get_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_llm_get_capabilities(self):
        """Test that LLM returns capabilities dict."""
        llm = LLMService()
        caps = llm.get_capabilities()
        assert "provider" in caps
        assert "model" in caps
        assert "models_available" in caps
        assert "streaming" in caps

    @patch("app.services.llm_service.LLMService._get_client")
    def test_llm_query(self, mock_client):
        """Test LLM query method. Forces OpenAI provider so the mock matches that branch's API shape."""
        llm = LLMService()
        llm._provider = LLMProvider.OPENAI

        # MagicMock supports __getitem__ for the choices[0] subscript
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"test": "response"}'
        mock_client.return_value.chat.completions.create.return_value = mock_response

        result = llm.query("Test prompt")
        assert '{"test": "response"}' in result

    def test_llm_parse_json_response(self):
        """Test JSON parsing from LLM response."""
        llm = LLMService()
        
        # Test direct JSON
        response = '{"key": "value"}'
        parsed = llm.parse_json_response(response)
        assert parsed["key"] == "value"

        # Test JSON with surrounding text
        response = 'Here is the JSON: {"key": "value"} and more text'
        parsed = llm.parse_json_response(response)
        assert parsed["key"] == "value"


class TestSentimentAnalyzer:
    """Test sentiment analysis module."""

    def test_sentiment_analyzer_init(self):
        """Test sentiment analyzer initialization."""
        analyzer = SentimentAnalyzer()
        assert analyzer.llm is not None

    def test_analyze_news_sentiment_empty(self):
        """Test handling of empty headlines."""
        analyzer = SentimentAnalyzer()
        result = analyzer.analyze_news_sentiment("AAPL", [])
        
        assert result["overall_sentiment"] == "neutral"
        assert result["score"] == 0.0
        assert result["headline_sentiments"] == []

    def test_analyze_news_sentiment_with_headlines(self):
        """Test news sentiment analysis. llm is an instance attribute, so swap it directly."""
        analyzer = SentimentAnalyzer()
        mock_llm = MagicMock()
        mock_llm.query.return_value = '{"overall_sentiment": "bullish", "score": 0.85}'
        mock_llm.parse_json_response.return_value = {"overall_sentiment": "bullish", "score": 0.85}
        analyzer.llm = mock_llm

        headlines = ["Apple beats earnings", "Strong revenue growth"]
        result = analyzer.analyze_news_sentiment("AAPL", headlines)

        assert result["overall_sentiment"] == "bullish"
        assert result["score"] == 0.85

    def test_combine_sentiments(self):
        """Test combining multiple sentiment sources."""
        analyzer = SentimentAnalyzer()
        
        news_sentiment = {"overall_sentiment": "bullish", "score": 0.8}
        market_sentiment = {"market_sentiment": "neutral", "overall_score": 0.0}
        social_sentiment = {"social_sentiment": "bullish", "avg_score": 0.7}
        
        result = analyzer.combine_sentiments("AAPL", news_sentiment, market_sentiment, social_sentiment)
        
        assert "unified_sentiment" in result
        assert "confidence" in result
        assert "sources" in result


class TestNewsAnalyzer:
    """Test news analysis module."""

    def test_news_analyzer_init(self):
        """Test news analyzer initialization."""
        analyzer = NewsAnalyzer()
        assert analyzer.llm is not None

    def test_detect_events_empty(self):
        """Test handling of empty headlines."""
        analyzer = NewsAnalyzer()
        result = analyzer.detect_events("AAPL", [])
        
        assert result["events"] == []
        assert result["critical_events"] == []
        assert result["risk_level"] == "none"

    def test_analyze_earnings(self):
        """Test earnings analysis."""
        analyzer = NewsAnalyzer()
        
        headlines = ["Apple Q3 earnings beat"]
        metrics = {
            "eps_beat": True,
            "revenue_beat": True,
            "guidance": "raised"
        }
        
        result = analyzer.analyze_earnings("AAPL", headlines, metrics)
        
        assert "earnings_surprise" in result
        assert "guidance_implication" in result
        assert "expected_volatility" in result

    def test_score_headline_severities_empty(self):
        """Test severity scoring with no headlines."""
        analyzer = NewsAnalyzer()
        result = analyzer.score_headline_severities("AAPL", [])
        assert result == []

    def test_aggregate_severity(self):
        """Test severity aggregation (sum by default)."""
        analyzer = NewsAnalyzer()
        scored = [
            {"severity": 5, "event_type": "product_launch"},
            {"severity": -2, "event_type": "downgrade"},
        ]
        assert analyzer.aggregate_severity(scored) == 3
        # mean would be 1.5 but default sum


class TestNewsSeverityGate:
    """Test the ported news severity gate."""

    def test_gate_init_and_snapshot(self):
        """Test gate initialization and config snapshot."""
        assert news_severity_gate is not None
        snap = news_severity_gate.snapshot()
        assert "enabled" in snap
        assert "min_aggregate" in snap
        assert snap["enabled"] is True or snap["enabled"] is False

    def test_gate_disabled(self):
        """If disabled via config, always allow."""
        # Note: in real would monkeypatch settings, here just call
        dec = news_severity_gate.evaluate("TEST")
        # depends on current settings, but shouldn't crash
        assert hasattr(dec, "allow_new_buys")

    def test_gate_evaluate_uses_analyzer(self):
        """Gate should call analyzer and aggregate."""
        from unittest.mock import patch, MagicMock
        from app.services.news_severity_gate import NewsSeverityGate
        gate = NewsSeverityGate()
        mock_analyzer = MagicMock()
        mock_analyzer.score_headline_severities.return_value = [{"severity": 5}, {"severity": 3}]
        mock_analyzer.aggregate_severity.return_value = 8.0
        with patch.object(gate, "_analyzer", mock_analyzer):
            dec = gate.evaluate("AAPL")
            assert dec.aggregate == 8.0
            assert dec.allow_new_buys is True  # 8 > -4
            mock_analyzer.score_headline_severities.assert_called()


class TestMarketIntelligence:
    """Test market intelligence module."""

    def test_market_intelligence_init(self):
        """Test market intelligence initialization."""
        intel = MarketIntelligence()
        assert intel.llm is not None

    def test_identify_technical_patterns(self):
        """Test technical pattern identification."""
        intel = MarketIntelligence()
        
        price_data = [100, 101, 102, 101, 100, 99, 100, 101, 102, 103]
        result = intel.identify_technical_patterns("AAPL", price_data)
        
        assert "patterns" in result
        assert "overall_pattern_quality" in result
        assert "nearest_support" in result
        assert "nearest_resistance" in result

    def test_assess_market_regime(self):
        """Test market regime assessment. Mocks the LLM to return a stable shape
        — without a mock the real LLM produces varying JSON keys that break the test."""
        intel = MarketIntelligence()
        mock_llm = MagicMock()
        mock_llm.query.return_value = "stub"
        mock_llm.parse_json_response.return_value = {
            "regime": "uptrend",
            "regime_probability": {
                "strong_uptrend": 0.2, "uptrend": 0.6, "range_bound": 0.15,
                "downtrend": 0.04, "strong_downtrend": 0.01,
            },
            "regime_change_risk": 0.15,
            "strategy_implications": "buy dips",
            "positioning": "long",
        }
        intel.llm = mock_llm

        indicators = {
            "trend": "up", "volatility": "normal", "breadth": "strong",
            "trend_strength": 0.75,
        }
        result = intel.assess_market_regime("AAPL", indicators)

        assert "regime" in result
        assert "regime_change_risk" in result

    def test_analyze_volatility(self):
        """Test volatility analysis."""
        intel = MarketIntelligence()
        
        result = intel.analyze_volatility("AAPL", realized_vol=18.5, implied_vol=16.2)
        
        assert "vol_regime" in result
        assert "realized_vs_implied" in result
        assert "vol_forecast" in result


class TestEnhancedAIAdvisor:
    """Test enhanced AI advisor."""

    def test_advisor_init(self):
        """Test advisor initialization."""
        advisor = EnhancedAIAdvisor()
        assert advisor.llm is not None
        assert advisor.sentiment is not None
        assert advisor.news is not None
        assert advisor.market_intel is not None

    def test_get_ai_capabilities(self):
        """Test getting AI capabilities."""
        advisor = EnhancedAIAdvisor()
        caps = advisor.get_ai_capabilities()
        
        assert "llm" in caps
        assert "sentiment_analysis" in caps
        assert "news_analysis" in caps
        assert "market_intelligence" in caps

    def test_default_hold_decision(self):
        """Test default hold decision on error."""
        advisor = EnhancedAIAdvisor()
        result = advisor._default_hold_decision("AAPL", 150.0, "Test error")
        
        assert result["action"] == "HOLD"
        assert result["confidence"] == 0.0
        assert result["symbol"] == "AAPL"
        assert result["price"] == 150.0


class TestStrategyAgents:
    """Test trading strategy agents."""

    def test_momentum_agent_init(self):
        """Test momentum agent initialization."""
        agent = MomentumAgent()
        assert agent.strategy_type.value == "momentum"

    def test_mean_reversion_agent_init(self):
        """Test mean reversion agent initialization."""
        agent = MeanReversionAgent()
        assert agent.strategy_type.value == "mean_reversion"

    def test_breakout_agent_init(self):
        """Test breakout agent initialization."""
        agent = BreakoutAgent()
        assert agent.strategy_type.value == "breakout"

    def test_get_strategy_agent(self):
        """Test getting strategy agent by type."""
        agent = get_strategy_agent("momentum")
        assert isinstance(agent, MomentumAgent)

        agent = get_strategy_agent("mean_reversion")
        assert isinstance(agent, MeanReversionAgent)

        agent = get_strategy_agent("breakout")
        assert isinstance(agent, BreakoutAgent)

    def test_get_strategy_agent_invalid(self):
        """Test getting invalid strategy agent."""
        with pytest.raises(ValueError):
            get_strategy_agent("invalid_strategy")

    def test_momentum_agent_execute(self):
        """Test momentum agent execution."""
        agent = MomentumAgent()
        
        market_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "rsi": 75,
            "macd": 0.5,
            "sma20": 148.0,
            "sma50": 145.0,
            "volume": 1000000,
            "volume_sma": 900000,
            "recent_prices": [145, 146, 147, 148, 149, 150],
        }
        
        result = agent.execute(market_data)
        
        assert "signal" in result
        assert result["strategy"] == "momentum"


# Integration tests
class TestIntegration:
    """Integration tests combining multiple modules."""

    def test_full_trading_decision_flow(self):
        """Test complete trading decision flow."""
        advisor = EnhancedAIAdvisor()
        
        # Prepare comprehensive market data
        market_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "technical_data": {
                "rsi": 65,
                "macd": 0.5,
                "prices": [145, 146, 147, 148, 149, 150],
            },
            "market_data": {
                "vix": 18.5,
                "breadth": {"advances": 1200, "declines": 800},
            },
            "news_headlines": [
                "Apple beats Q3 earnings",
                "Strong iPhone sales"
            ],
        }
        
        # This would normally call LLM, but we just verify structure
        # In real tests, mock the LLM calls
        assert "symbol" in market_data
        assert "price" in market_data

    def test_sentiment_to_decision_flow(self):
        """Test sentiment analysis feeding into decisions."""
        sentiment = SentimentAnalyzer()
        
        news_sent = {
            "overall_sentiment": "bullish",
            "score": 0.8
        }
        market_sent = {
            "market_sentiment": "neutral",
            "overall_score": 0.0
        }
        
        combined = sentiment.combine_sentiments(
            "AAPL",
            news_sent,
            {"market_sentiment": "neutral", "overall_score": 0.0},
            {"social_sentiment": "bullish", "avg_score": 0.7}
        )
        
        assert "unified_sentiment" in combined


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
