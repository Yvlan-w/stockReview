// ============================================================
// 模拟数据 / 分享版注入数据 / 默认持仓（无依赖）
// ============================================================

export const mockData = {
    // ---- Portfolio Summary ----
    summary: {
        totalAssets: 1234567.89,
        todayPnL: 12345.67,
        todayPnLPct: 1.02,
        portfolioReturn: 23.45,
        availableCash: 234567.89
    },

    // ---- Positions (12 stocks) ----
    positions: [
        { name: '贵州茅台', code: '600519', quantity: 100, costPrice: 1680.00, price: 1756.80, sector: '消费' },
        { name: '宁德时代', code: '300750', quantity: 500, costPrice: 185.50, price: 198.20, sector: '新能源' },
        { name: '比亚迪', code: '002594', quantity: 800, costPrice: 245.00, price: 232.50, sector: '新能源' },
        { name: '中国平安', code: '601318', quantity: 2000, costPrice: 45.20, price: 48.60, sector: '金融' },
        { name: '招商银行', code: '600036', quantity: 3000, costPrice: 32.80, price: 35.40, sector: '金融' },
        { name: '迈瑞医疗', code: '300760', quantity: 300, costPrice: 298.00, price: 315.60, sector: '医疗' },
        { name: '隆基绿能', code: '601012', quantity: 1500, costPrice: 28.50, price: 24.30, sector: '光伏' },
        { name: '中信证券', code: '600030', quantity: 2500, costPrice: 19.80, price: 21.30, sector: '金融' },
        { name: '五粮液', code: '000858', quantity: 400, costPrice: 145.00, price: 152.80, sector: '消费' },
        { name: '立讯精密', code: '002475', quantity: 1200, costPrice: 28.90, price: 32.15, sector: '科技' },
        { name: '药明康德', code: '603259', quantity: 350, costPrice: 72.50, price: 68.40, sector: '医疗' },
        { name: '紫金矿业', code: '601899', quantity: 4000, costPrice: 15.20, price: 17.85, sector: '资源' },
    ],

    // ---- 30-day return curve data ----
    returns30d: [
        { date: '07-12', value: 0, benchmark: 0 },
        { date: '07-13', value: 0.8, benchmark: 0.3 },
        { date: '07-14', value: 1.2, benchmark: 0.5 },
        { date: '07-15', value: 0.9, benchmark: 0.2 },
        { date: '07-16', value: 1.8, benchmark: 0.8 },
        { date: '07-19', value: 2.1, benchmark: 1.0 },
        { date: '07-20', value: 1.9, benchmark: 0.9 },
        { date: '07-21', value: 2.5, benchmark: 1.2 },
        { date: '07-22', value: 3.2, benchmark: 1.5 },
        { date: '07-23', value: 2.8, benchmark: 1.3 },
        { date: '07-26', value: 3.5, benchmark: 1.8 },
        { date: '07-27', value: 4.1, benchmark: 2.0 },
        { date: '07-28', value: 3.8, benchmark: 1.7 },
        { date: '07-29', value: 4.5, benchmark: 2.2 },
        { date: '07-30', value: 5.2, benchmark: 2.5 },
        { date: '08-02', value: 4.8, benchmark: 2.3 },
        { date: '08-03', value: 5.5, benchmark: 2.8 },
        { date: '08-04', value: 6.2, benchmark: 3.0 },
        { date: '08-05', value: 5.8, benchmark: 2.6 },
        { date: '08-06', value: 6.5, benchmark: 3.2 },
        { date: '08-09', value: 7.2, benchmark: 3.5 },
        { date: '08-10', value: 6.8, benchmark: 3.3 },
        { date: '08-11', value: 7.5, benchmark: 3.8 },
        { date: '08-11', value: 8.2, benchmark: 4.0 },
        { date: '08-11', value: 7.8, benchmark: 3.7 },
        { date: '08-11', value: 8.5, benchmark: 4.2 },
        { date: '08-11', value: 9.2, benchmark: 4.5 },
        { date: '08-11', value: 8.8, benchmark: 4.3 },
        { date: '08-11', value: 9.5, benchmark: 4.8 },
        { date: '08-11', value: 10.2, benchmark: 5.0 },
        { date: '08-11', value: 9.8, benchmark: 4.7 },
    ],

    // ---- Daily P&L (7 days) ----
    dailyPnL: [
        { date: '周一 08/05', value: 5678.90 },
        { date: '周二 08/06', value: -2345.60 },
        { date: '周三 08/07', value: 8923.45 },
        { date: '周四 08/08', value: 3456.78 },
        { date: '周五 08/09', value: -1234.50 },
        { date: '周一 08/10', value: 6789.12 },
        { date: '周二 08/11', value: 12345.67 },
    ],

    // ---- Strategy / Trade Records ----
    trades: [
        { id: 1, time: '2025-08-11 14:25:33', strategy: '均线突破策略', type: 'buy', stock: '贵州茅台', code: '600519', price: 1756.80, quantity: 50, result: 'success', note: '20日均线突破确认，放量上涨' },
        { id: 2, time: '2025-08-11 10:15:22', strategy: '趋势跟随策略', type: 'sell', stock: '隆基绿能', code: '601012', price: 24.30, quantity: 500, result: 'success', note: '跌破止损位，执行止损操作' },
        { id: 3, time: '2025-08-08 13:45:10', strategy: '价值回归策略', type: 'buy', stock: '招商银行', code: '600036', price: 35.40, quantity: 1000, result: 'success', note: 'PB估值处于历史低位，逢低建仓' },
        { id: 4, time: '2025-08-07 09:35:55', strategy: '事件驱动策略', type: 'buy', stock: '比亚迪', code: '002594', price: 235.00, quantity: 200, result: 'partial', note: '海外销量超预期，部分成交' },
        { id: 5, time: '2025-08-06 14:50:18', strategy: '均线突破策略', type: 'sell', stock: '药明康德', code: '603259', price: 68.40, quantity: 150, result: 'success', note: '反弹至压力位，获利了结' },
        { id: 6, time: '2025-08-05 10:20:44', strategy: '动量轮动策略', type: 'buy', stock: '立讯精密', code: '002475', price: 31.50, quantity: 500, result: 'success', note: '苹果链订单预期向好，动量信号触发' },
        { id: 7, time: '2025-08-04 11:05:33', strategy: '趋势跟随策略', type: 'sell', stock: '紫金矿业', code: '601899', price: 17.20, quantity: 1000, result: 'success', note: '金价高位震荡，减仓锁定利润' },
        { id: 8, time: '2025-08-03 13:30:21', strategy: '价值回归策略', type: 'buy', stock: '迈瑞医疗', code: '300760', price: 310.00, quantity: 100, result: 'success', note: '业绩稳健增长，估值合理区间' },
        { id: 9, time: '2025-08-01 09:42:15', strategy: '事件驱动策略', type: 'buy', stock: '中信证券', code: '600030', price: 20.80, quantity: 800, result: 'failed', note: '牛市旗手预期，但价格未触及' },
        { id: 10, time: '2025-07-30 14:15:50', strategy: '均线突破策略', type: 'sell', stock: '五粮液', code: '000858', price: 150.50, quantity: 100, result: 'success', note: '消费复苏不及预期，止盈离场' },
    ],

    // ---- Strategy Summary Stats ----
    strategyStats: {
        totalTrades: 156,
        winRate: 68.5,
        avgProfit: 2856.78,
        maxDrawdown: -8.32,
        profitFactor: 2.15,
        sharpeRatio: 1.82
    },

    // ---- Market News ----
    news: [
        { id: 1, time: '10分钟前', source: '财联社', title: '央行开展1500亿元7天期逆回购操作，中标利率维持1.70%', summary: '中国人民银行今日开展1500亿元7天期逆回购操作，中标利率为1.70%，与此前持平。市场流动性保持合理充裕。', tags: ['货币政策', '流动性'] },
        { id: 2, time: '35分钟前', source: '新浪财经', title: '工信部：加快推进新能源汽车产业发展，支持固态电池技术攻关', summary: '工业和信息化部表示将加快新能源汽车产业发展步伐，重点支持固态电池等前沿技术研发和产业化应用。', tags: ['新能源', '产业政策'] },
        { id: 3, time: '1小时前', source: '东方财富', title: 'A股三大指数集体高开，创业板指涨超1%', summary: '今日早盘A股三大指数集体高开，沪指涨0.52%，深成指涨0.85%，创业板指涨1.12%。两市超3200只个股上涨。', tags: ['A股', '开盘行情'] },
        { id: 4, time: '2小时前', source: '证券时报', title: '北向资金净流入超50亿元，连续三日净买入', summary: '截至发稿，北向资金今日净流入52.3亿元，其中沪股通净流入28.7亿元，深股通净流入23.6亿元。', tags: ['北向资金', '外资'] },
        { id: 5, time: '3小时前', source: '第一财经', title: '国务院常务会议：研究推动大规模设备更新和消费品以旧换新', summary: '会议指出要加大力度推动大规模设备更新和消费品以旧换新，进一步释放内需潜力，促进经济持续回升向好。', tags: ['宏观政策', '内需'] },
        { id: 6, time: '4小时前', source: '华尔街见闻', title: '美联储官员释放鸽派信号，市场预期9月降息概率升至75%', summary: '多位美联储官员近期发表讲话，暗示通胀已明显放缓，为降息打开空间。CME FedWatch工具显示9月降息概率升至75%。', tags: ['美联储', '全球市场'] },
        { id: 7, time: '5小时前', source: '财联社', title: '半导体板块持续活跃，多只个股涨停', summary: '受国产替代加速预期影响，半导体板块今日表现强势，多只个股涨停封板，板块整体涨幅超3%。', tags: ['半导体', '科技'] },
        { id: 8, time: '6小时前', source: '经济日报', title: '上半年GDP同比增长5.0%，经济运行总体平稳', summary: '国家统计局数据显示，上半年国内生产总值616836亿元，按不变价格计算，同比增长5.0%，经济延续恢复向好态势。', tags: ['宏观经济', 'GDP'] },
    ],

    // ---- People's Daily News Analysis ----
    peoplesDaily: [
        {
            title: '中共中央政治局召开会议 分析研究当前经济形势',
            analysis: '会议强调要坚持稳中求进工作总基调，完整、准确、全面贯彻新发展理念，加快构建新发展格局，着力推动高质量发展。会议指出当前经济运行面临新的困难挑战，要加大宏观政策调控力度，积极扩大国内需求。',
            impact: 'bullish',
            impactText: '利好',
            keyPoints: ['加大宏观调控力度', '扩大内需', '防范化解风险']
        },
        {
            title: '关于进一步优化外商投资环境 加大吸引外商投资力度的意见',
            analysis: '国务院印发意见提出24条具体措施，包括提高利用外资质量、保障外商投资企业国民待遇、持续加强外商投资保护等。这表明中国坚定不移推进高水平对外开放的决心。',
            impact: 'bullish',
            impactText: '重大利好',
            keyPoints: ['保障国民待遇', '加强投资保护', '高水平开放']
        },
        {
            title: '推动金融高质量发展 建设金融强国',
            analysis: '中央金融工作会议强调，要加快建设金融强国，全面加强金融监管，完善金融体制，优化金融服务，防范化解风险，坚定不移走中国特色金融发展之路。',
            impact: 'neutral',
            impactText: '中性偏多',
            keyPoints: ['金融强国目标', '加强监管', '防范风险']
        },
    ],

    // ---- Market Indices ----
    indices: [
        { name: '上证指数', value: 2876.53, change: 0.82, pct: '+0.82%' },
        { name: '深证成指', value: 8965.28, change: 1.15, pct: '+1.15%' },
        { name: '创业板指', value: 1723.45, change: 1.42, pct: '+1.42%' },
        { name: '科创50', value: 756.32, change: 0.65, pct: '+0.65%' },
    ],

    // ---- 今日盯大盘 (Today's Market Overview) ----
    marketOverview: {
        indices: [
            { name: '上证指数', value: 2876.53, prevClose: 2853.10, change: 23.43, changePct: 0.82 },
            { name: '深证成指', value: 8965.28, prevClose: 8863.20, change: 102.08, changePct: 1.15 },
            { name: '创业板指', value: 1723.45, prevClose: 1699.30, change: 24.15, changePct: 1.42 },
        ],
        totalVolume: 8567,
        prevVolume: 7892,
        advCount: 3256,
        decCount: 1684,
        flatCount: 128,
        totalStocks: 5068,
    },

    // ---- 上证指数近一月成交量 ----
    shVolume30d: [
        { date: '07-12', volume: 3245 }, { date: '07-15', volume: 3578 },
        { date: '07-16', volume: 2890 }, { date: '07-17', volume: 4120 },
        { date: '07-18', volume: 3650 }, { date: '07-19', volume: 4280 },
        { date: '07-22', volume: 3950 }, { date: '07-23', volume: 4830 },
        { date: '07-24', volume: 5670 }, { date: '07-25', volume: 5120 },
        { date: '07-26', volume: 4450 }, { date: '07-29', volume: 4980 },
        { date: '07-30', volume: 5340 }, { date: '07-31', volume: 6210 },
        { date: '08-01', volume: 5890 }, { date: '08-02', volume: 5230 },
        { date: '08-05', volume: 6450 }, { date: '08-06', volume: 7120 },
        { date: '08-07', volume: 6780 }, { date: '08-08', volume: 7340 },
        { date: '08-09', volume: 6890 }, { date: '08-12', volume: 7560 },
        { date: '08-13', volume: 8120 }, { date: '08-14', volume: 7890 },
        { date: '08-15', volume: 8450 }, { date: '08-16', volume: 9230 },
        { date: '08-19', volume: 8780 }, { date: '08-20', volume: 9450 },
        { date: '08-21', volume: 8567 }, { date: '08-22', volume: 8567 },
    ],

    // ---- 板块表现（今日涨跌幅） ----
    sectorPerformance: [
        { name: '半导体',     pct: 4.32, leader: '中芯国际', leaderPct: 9.85 },
        { name: '军工',       pct: 3.56, leader: '中航沈飞', leaderPct: 8.42 },
        { name: '消费电子',   pct: 2.89, leader: '立讯精密', leaderPct: 7.15 },
        { name: '新能源',     pct: 2.15, leader: '宁德时代', leaderPct: 5.68 },
        { name: '医疗器械',   pct: 1.72, leader: '迈瑞医疗', leaderPct: 4.32 },
        { name: '证券',       pct: 1.05, leader: '中信证券', leaderPct: 3.56 },
        { name: '银行',       pct: 0.38, leader: '招商银行', leaderPct: 1.85 },
        { name: '白酒',       pct: -0.45, leader: '贵州茅台', leaderPct: -1.12 },
        { name: '光伏',       pct: -1.23, leader: '隆基绿能', leaderPct: -3.45 },
        { name: '地产',       pct: -2.15, leader: '万科A',   leaderPct: -4.68 },
    ],

    // ---- 高低切 / 领涨方向 ----
    leadershipAnalysis: [
        {
            tag: '高位方向',
            tagColor: 'up',
            title: '半导体 · 军工双主线领涨',
            desc: '半导体板块受国产替代加速预期驱动，板块涨幅 4.32% 领跑全场；军工板块受装备交付周期提速催化，连续 3 日资金净流入。高位板块呈现「科技+安全」双轮驱动格局。',
            stocks: ['中芯国际(+9.85%)', '北方华创(+7.62%)', '中航沈飞(+8.42%)']
        },
        {
            tag: '低位切换',
            tagColor: 'warning',
            title: '资金高低切：白酒、光伏承压',
            desc: '前期超跌的白酒、光伏板块继续探底，部分资金从高位科技板块流出，流入低位超跌品种。但切换力度有限，市场仍以强势方向为主，低位板块需等待企稳信号。',
            stocks: ['贵州茅台(-1.12%)', '隆基绿能(-3.45%)', '万科A(-4.68%)']
        },
        {
            tag: '领涨逻辑',
            tagColor: 'primary',
            title: '科技自主可控 + 国防安全',
            desc: '今日领涨方向聚焦「自主可控」与「国防安全」两大主线。半导体设备国产化率提升预期升温，军工十四五末期装备交付加速，两条线均受政策与业绩双重驱动。',
            stocks: []
        }
    ],

    // ---- 核心驱动因素 ----
    driverAnalysis: [
        {
            factor: '政策催化',
            icon: '🏛️',
            desc: '国务院常务会议研究推进科技自立自强，半导体设备进口替代政策预期升温，直接催化科技板块走强。同时军工采购节奏加快，装备交付进入加速期。',
            impact: 'bullish'
        },
        {
            factor: '资金面',
            icon: '💰',
            desc: '两市成交额 8567 亿，较前一日放量 675 亿，增量资金主要流向科技与军工。北向资金连续 3 日净流入，今日净买入 52.3 亿，重点加仓半导体龙头。',
            impact: 'bullish'
        },
        {
            factor: '情绪面',
            icon: '📊',
            desc: '上涨家数 3256 家（64.2%），市场赚钱效应回升。涨停个股 68 只，连板高度升至 5 板，短线情绪活跃。跌停仅 12 只，抛压明显减轻。',
            impact: 'bullish'
        },
        {
            factor: '风险提示',
            icon: '⚠️',
            desc: '高位科技板块短期涨幅较大，存在技术性回调压力。美联储 9 月降息预期已充分定价，若落地不及预期可能引发全球风险资产波动。注意控制仓位。',
            impact: 'warning'
        }
    ]
};

// 分享版注入标记：由 index.html 内联脚本挂载到 window 上，
// exportShareSnapshot() 生成分享版时会将其替换为当前真实数据。
export const EMBEDDED_POSITIONS = window.EMBEDDED_POSITIONS;
export const EMBEDDED_PROFILE = window.EMBEDDED_PROFILE;
export const EMBEDDED_USERDATA = window.EMBEDDED_USERDATA;

// 默认持仓（首次访问或重置时使用）
export const DEFAULT_POSITIONS = [
    { name: '贵州茅台', code: '600519', quantity: 100, costPrice: 1680.00, price: 1756.80, sector: '消费' },
    { name: '宁德时代', code: '300750', quantity: 500, costPrice: 185.50, price: 198.20, sector: '新能源' },
    { name: '比亚迪', code: '002594', quantity: 800, costPrice: 245.00, price: 232.50, sector: '新能源' },
    { name: '中国平安', code: '601318', quantity: 2000, costPrice: 45.20, price: 48.60, sector: '金融' },
    { name: '招商银行', code: '600036', quantity: 3000, costPrice: 32.80, price: 35.40, sector: '金融' },
    { name: '迈瑞医疗', code: '300760', quantity: 300, costPrice: 298.00, price: 315.60, sector: '医疗' },
    { name: '隆基绿能', code: '601012', quantity: 1500, costPrice: 28.50, price: 24.30, sector: '光伏' },
    { name: '中信证券', code: '600030', quantity: 2500, costPrice: 19.80, price: 21.30, sector: '金融' },
    { name: '五粮液', code: '000858', quantity: 400, costPrice: 145.00, price: 152.80, sector: '消费' },
    { name: '立讯精密', code: '002475', quantity: 1200, costPrice: 28.90, price: 32.15, sector: '科技' },
    { name: '药明康德', code: '603259', quantity: 350, costPrice: 72.50, price: 68.40, sector: '医疗' },
    { name: '紫金矿业', code: '601899', quantity: 4000, costPrice: 15.20, price: 17.85, sector: '资源' },
];
