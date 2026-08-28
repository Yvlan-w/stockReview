// ============================================================
// 常量与配置（无依赖，最先加载）
// ============================================================

// 行情数据源（东方财富）
export const MARKET_APIS = {
    // push2.eastmoney.com 已逐步迁移到 push2delay.eastmoney.com，保留主/备双地址容错
    realtime: [
        'https://push2.eastmoney.com/api/qt/ulist.np/get',
        'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
    ],
    kline: 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
};

// 东方财富行情接口 token（缺省可能导致 push2his 返回 ERR_EMPTY_RESPONSE）
export const EASTMONEY_UT = 'fa5fd1943c7b386f172d6893dbfba10b';

export const MARKET_SECIDS = '1.000001,0.399001,0.399006'; // 上证指数,深证成指,创业板指
export const SH_INDEX_SECID = '1.000001';
export const REALTIME_FIELDS = 'f2,f3,f4,f5,f6,f7,f12,f13,f14,f104,f105,f106,f107,f108,f152';

// 持仓弹窗板块下拉选项
export const SECTORS = ['消费', '金融', '科技', '医疗', '新能源', '光伏', '资源', '军工', '半导体', '地产', '化工', '其他'];

// localStorage 键名
export const POSITIONS_STORAGE_KEY = 'stock_review_positions';
export const USERDATA_STORAGE_KEY = 'stock_review_userdata';
export const CLIENTS_STORAGE_KEY = 'stock_review_clients_v3';
export const PROFILE_STORAGE_KEY = 'stock_review_user_profile';

// 客户工作台数据生成
export const CLIENTS_COUNT = 100;
export const RISK_LEVELS = ['保守型', '稳健型', '平衡型', '积极型', '激进型'];
export const RISK_WEIGHTS = [0.18, 0.28, 0.26, 0.18, 0.10];
export const CLIENT_TAGS_POOL = ['VIP', '高净值', '待跟进', '新客户', '活跃', '稳健偏好'];
export const CLIENT_NOTES_POOL = ['偏好低波动，关注高股息分红', '关注新能源与科技成长', '近期考虑增加债券配置', '风险承受能力较强，可谈权益加仓', '退休规划中，关注现金流', '希望平衡收益与回撤', '', '', ''];
export const CLIENT_SURNAMES = ['张','李','王','刘','陈','杨','赵','黄','周','吴','徐','孙','胡','朱','高','林','何','郭','马','罗','梁','宋','郑','谢','韩','唐','冯','于','董','萧','程','曹','袁','邓','许','傅','沈','曾','彭','吕','苏','卢','蒋','蔡','贾','丁','魏','薛','叶','阎','余','潘','杜','戴','夏','钟','汪','田','任','姜','范','方','石','姚','谭','廖','邹','熊','金','陆','郝','孔','白','崔','康','毛','邱','秦','江','史','顾','侯','邵','孟','龙','万','段','雷','钱','汤','尹','黎','易','常','武','乔','贺','赖','龚','文'];
export const CLIENT_GIVEN = ['伟','芳','娜','敏','静','磊','军','洋','勇','艳','杰','娟','涛','明','超','秀英','霞','平','刚','桂英','文','辉','力','建华','玉兰','海燕','志强','丽','斌','国庆','红','雪','婷','浩','宇','子涵','雨欣','浩然','思远','嘉怡','梓萱','一诺','俊杰','欣怡','睿','泽','晨','可','欣然','若曦','沐宸','诗涵','宇轩','雅静','明轩','梦琪','思源','立诚','美琳','家豪','欣妍','鹏','晨曦','博文','语嫣','国豪','淑华','佳琪','永强','秀兰','金凤','建国','玉华','春梅','志明','丽华','瑞','国栋','晓东','桂芳','海峰','淑珍','文静','子轩','雨泽','雅琪','晓明','天翊','思琪','浩然','宇辰','雨桐','嘉豪'];
export const STOCK_POOL = [
    { name: '贵州茅台', code: '600519', sector: '消费', base: 1680 },
    { name: '宁德时代', code: '300750', sector: '新能源', base: 198 },
    { name: '比亚迪', code: '002594', sector: '新能源', base: 232 },
    { name: '中国平安', code: '601318', sector: '金融', base: 48 },
    { name: '招商银行', code: '600036', sector: '金融', base: 35 },
    { name: '迈瑞医疗', code: '300760', sector: '医疗', base: 315 },
    { name: '隆基绿能', code: '601012', sector: '光伏', base: 24 },
    { name: '中信证券', code: '600030', sector: '金融', base: 21 },
    { name: '五粮液', code: '000858', sector: '消费', base: 152 },
    { name: '立讯精密', code: '002475', sector: '科技', base: 32 },
    { name: '药明康德', code: '603259', sector: '医疗', base: 68 },
    { name: '紫金矿业', code: '601899', sector: '资源', base: 17 },
    { name: '恒瑞医药', code: '600276', sector: '医疗', base: 45 },
    { name: '美的集团', code: '000333', sector: '家电', base: 58 },
    { name: '海天味业', code: '603288', sector: '消费', base: 42 },
    { name: '中芯国际', code: '688981', sector: '科技', base: 52 },
    { name: '三一重工', code: '600031', sector: '机械', base: 16 },
    { name: '万华化学', code: '600309', sector: '化工', base: 78 },
    { name: '长江电力', code: '600900', sector: '公用', base: 24 },
    { name: '中国中免', code: '601888', sector: '消费', base: 82 },
    { name: '科大讯飞', code: '002230', sector: '科技', base: 44 },
    { name: '北方华创', code: '002371', sector: '半导体', base: 240 },
    { name: '山西汾酒', code: '600809', sector: '消费', base: 210 },
    { name: '牧原股份', code: '002714', sector: '农业', base: 38 },
    { name: '东方财富', code: '300059', sector: '金融', base: 14 },
    { name: '京东方A', code: '000725', sector: '电子', base: 4.2 },
    { name: '中国石油', code: '601857', sector: '能源', base: 8.6 },
    { name: '海尔智家', code: '600690', sector: '家电', base: 26 },
];

// 服务关系池（为身份权限管理预留接口：客户 → 1 顾问 + 1~2 客服）
export const ADVISOR_POOL = [
    { id: 'adv_001', name: '顾问·张伟' },
    { id: 'adv_002', name: '顾问·李娜' },
    { id: 'adv_003', name: '顾问·王强' },
    { id: 'adv_004', name: '顾问·刘敏' },
    { id: 'adv_005', name: '顾问·陈静' },
];
export const SERVICE_STAFF_POOL = [
    { id: 'svc_001', name: '客服·赵芳' },
    { id: 'svc_002', name: '客服·钱磊' },
    { id: 'svc_003', name: '客服·孙婷' },
    { id: 'svc_004', name: '客服·周明' },
    { id: 'svc_005', name: '客服·吴娜' },
    { id: 'svc_006', name: '客服·郑浩' },
];

// 客户风险等级徽章样式
export const RISK_BADGE = {
    '保守型': 'bg-primary/10 text-primary',
    '稳健型': 'bg-positive/10 text-positive',
    '平衡型': 'bg-warning/10 text-warning',
    '积极型': 'bg-orange-100 text-orange-600',
    '激进型': 'bg-negative/10 text-negative'
};

// ============================================================
// 客户标签体系（受控分类 + 自定义标签共存）
// 供开户向导、客户资料卡「标签编辑」弹窗、客户筛选器共用。
// 受控标签分 5 组：
//   1) 资金         单选
//   2) 偏好         单选
//   3) 策略限制     多选（可多选）
//   4) 交易频次     单选
//   5) 自定义标签   用户自由输入，不属于受控集合即视为自定义
// ============================================================
export const CLIENT_TAG_GROUPS = [
    {
        key: 'assets', label: '资金', type: 'single',
        options: [
            '0-10万', '10万-50万', '50万-100万', '100万-500万', '500万-1000万', '1000万以上',
        ],
    },
    {
        key: 'preference', label: '偏好', type: 'single',
        options: ['低波动偏好', '接受中等波动', '高波动偏好'],
    },
    {
        key: 'constraint', label: '策略限制', type: 'multi',
        options: [
            '无A股账户', '无沪深交易权限', '无创业板权限',
            '无科创板权限', '无北交所权限',
        ],
    },
    {
        key: 'frequency', label: '交易频次', type: 'single',
        options: ['高频交易', '波段交易', '低频投资'],
    },
];
export const ALL_MANAGED_TAGS = CLIENT_TAG_GROUPS.flatMap(g => g.options);
