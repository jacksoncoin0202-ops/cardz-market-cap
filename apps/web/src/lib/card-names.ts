import type { Locale } from "./types";

type LocalizedName = Partial<Record<Locale, string>>;

// English names the upstream catalog stores truncated or misspelled. `canonical_name` cannot
// be repaired in the database because it feeds `opaque_id`, so editing it would orphan the
// card's price and population history; the repair belongs here, in the display layer. Each
// replacement is backed by an approved local identity receipt, never guessed:
//   Monkey.D.Luff    -> canonical English identity receipt
//   Okuge            -> canonical English identity receipt
//   Ethan's Ho       -> catalog_variant 732/742/746, the English printings of the same card
//   3th Anniversary  -> "3th" is not an English ordinal; the printing is the 3rd Anniversary
const EN_DISPLAY: Record<string, string> = {
  "Monkey.D.Luff": "Monkey.D.Luffy",
  "Okuge": "Okuge-sama and Maiko-han Pikachu",
  "Ethan's Ho": "Ethan's Ho-Oh ex",
  "Monkey.D.Luffy SEC-SPC 3th Anniversary Special Card Booster Pack A Fist of Divine Speed":
    "Monkey.D.Luffy SEC-SPC 3rd Anniversary Special Card Booster Pack A Fist of Divine Speed",
};

export function displayCardNameEn(englishName: string): string {
  return EN_DISPLAY[englishName.trim()] ?? englishName;
}

// Verified canonical names for Pokémon species, One Piece characters and known
// special printings. Unlisted names fall back to English upstream until the
// data pipeline ships full four-locale editorial names.
const EXACT: Record<string, LocalizedName> = {
"Monkey D. Luffy": { "zh-TW": "蒙其・D・魯夫", "zh-CN": "蒙奇・D・路飞", ja: "モンキー・D・ルフィ" },
  "Portgas D. Ace": { "zh-TW": "波特卡斯・D・艾斯", "zh-CN": "波特卡斯・D・艾斯", ja: "ポートガス・D・エース" },
  "Portgas D. Ace (Error)": { "zh-TW": "波特卡斯・D・艾斯（錯版）", "zh-CN": "波特卡斯・D・艾斯（错版）", ja: "ポートガス・D・エース（エラー）" },
  "Gol D. Roger": { "zh-TW": "哥爾・D・羅傑", "zh-CN": "哥尔・D・罗杰", ja: "ゴール・D・ロジャー" },
  "Marshall D. Teach": { "zh-TW": "馬歇爾・D・汀奇", "zh-CN": "马歇尔・D・蒂奇", ja: "マーシャル・D・ティーチ" },
  "Yamato": { "zh-TW": "大和", "zh-CN": "大和", ja: "ヤマト" },
  "Buggy": { "zh-TW": "巴基", "zh-CN": "巴基", ja: "バギー" },
  "Donquixote Doflamingo": { "zh-TW": "唐吉訶德・多佛朗明哥", "zh-CN": "唐吉诃德・多弗拉门戈", ja: "ドンキホーテ・ドフラミンゴ" },
  "Ms. All Sunday": { "zh-TW": "Miss All Sunday", "zh-CN": "Miss All Sunday", ja: "ミス・オールサンデー" },
  "O-Nami": { "zh-TW": "小紫", "zh-CN": "小紫", ja: "おナミ" },
  "Rebecca": { "zh-TW": "蕾貝卡", "zh-CN": "蕾贝卡", ja: "レベッカ" },
  "Terapagos ex": { "zh-TW": "太樂巴戈斯ex", "zh-CN": "太乐巴戈斯ex", ja: "テラパゴスex" },
  "Raging Bolt ex": { "zh-TW": "破空焰ex", "zh-CN": "破空焰ex", ja: "ウガツホムラex" },
  "Iron Crown ex": { "zh-TW": "鐵武者ex", "zh-CN": "铁武者ex", ja: "テツノカシラex" },
  "Team Rocket's Mewtwo ex": { "zh-TW": "火箭隊的超夢ex", "zh-CN": "火箭队的超梦ex", ja: "ロケット団のミュウツーex" },
  "Team Rocket's Crobat ex": { "zh-TW": "火箭隊的叉字蝠ex", "zh-CN": "火箭队的叉字蝠ex", ja: "ロケット団のクロバットex" },
  "Cynthia's Garchomp ex": { "zh-TW": "竹蘭的烈咬陸鯊ex", "zh-CN": "竹兰的烈咬陆鲨ex", ja: "シロナのガブリアスex" },
  "N's Zoroark ex": { "zh-TW": "N的索羅亞克ex", "zh-CN": "N的索罗亚克ex", ja: "Nのゾロアークex" },
  "Single Strike Urshifu VMAX": { "zh-TW": "一擊武道熊師VMAX", "zh-CN": "一击武道熊师VMAX", ja: "いちげきウーラオスVMAX" },
  "Mega Charizard X ex": { "zh-TW": "超級噴火龍X ex", "zh-CN": "超级喷火龙X ex", ja: "メガリザードンX ex" },
  "Mega Charizard Y ex": { "zh-TW": "超級噴火龍Y ex", "zh-CN": "超级喷火龙Y ex", ja: "メガリザードンY ex" },
  "Charizard ex": { "zh-TW": "噴火龍ex", "zh-CN": "喷火龙ex", ja: "リザードンex" },
  "Umbreon ex": { "zh-TW": "月亮伊布ex", "zh-CN": "月亮伊布ex", ja: "ブラッキーex" },
  "Umbreon VMAX": { "zh-TW": "月亮伊布VMAX", "zh-CN": "月亮伊布VMAX", ja: "ブラッキーVMAX" },
  "Umbreon VMAX (Alternate Art Secret)": { "zh-TW": "月亮伊布VMAX（異圖秘密）", "zh-CN": "月亮伊布VMAX（异图秘密）", ja: "ブラッキーVMAX（スペシャルアート）" },
  "Mew ex": { "zh-TW": "夢幻ex", "zh-CN": "梦幻ex", ja: "ミュウex" },
  "Pikachu ex": { "zh-TW": "皮卡丘ex", "zh-CN": "皮卡丘ex", ja: "ピカチュウex" },
  "Pikachu VMAX": { "zh-TW": "皮卡丘VMAX", "zh-CN": "皮卡丘VMAX", ja: "ピカチュウVMAX" },
  "Pikachu V": { "zh-TW": "皮卡丘V", "zh-CN": "皮卡丘V", ja: "ピカチュウV" },
  "Eevee ex": { "zh-TW": "伊布ex", "zh-CN": "伊布ex", ja: "イーブイex" },
  "Sylveon ex": { "zh-TW": "仙子伊布ex", "zh-CN": "仙子伊布ex", ja: "ニンフィアex" },
  "Espeon ex": { "zh-TW": "太陽伊布ex", "zh-CN": "太阳伊布ex", ja: "エーフィex" },
  "Leafeon ex": { "zh-TW": "葉伊布ex", "zh-CN": "叶伊布ex", ja: "リーフィアex" },
  "Glaceon ex": { "zh-TW": "冰伊布ex", "zh-CN": "冰伊布ex", ja: "グレイシアex" },
  "Vaporeon ex": { "zh-TW": "水伊布ex", "zh-CN": "水伊布ex", ja: "シャワーズex" },
  "Flareon ex": { "zh-TW": "火伊布ex", "zh-CN": "火伊布ex", ja: "ブースターex" },
  "Jolteon ex": { "zh-TW": "雷伊布ex", "zh-CN": "雷伊布ex", ja: "サンダースex" },
  "Zapdos ex": { "zh-TW": "閃電鳥ex", "zh-CN": "闪电鸟ex", ja: "サンダーex" },
  "Zekrom ex": { "zh-TW": "捷克羅姆ex", "zh-CN": "捷克罗姆ex", ja: "ゼクロムex" },
  "Reshiram ex": { "zh-TW": "萊希拉姆ex", "zh-CN": "莱希拉姆ex", ja: "レシラムex" },
  "Latias ex": { "zh-TW": "拉帝亞斯ex", "zh-CN": "拉帝亚斯ex", ja: "ラティアスex" },
  "Blastoise ex": { "zh-TW": "水箭龜ex", "zh-CN": "水箭龟ex", ja: "カメックスex" },
  "Venusaur ex": { "zh-TW": "妙蛙花ex", "zh-CN": "妙蛙花ex", ja: "フシギバナex" },
  "Alakazam ex": { "zh-TW": "胡地ex", "zh-CN": "胡地ex", ja: "フーディンex" },
  "Bulbasaur": { "zh-TW": "妙蛙種子", "zh-CN": "妙蛙种子", ja: "フシギダネ" },
  "Ivysaur": { "zh-TW": "妙蛙草", "zh-CN": "妙蛙草", ja: "フシギソウ" },
  "Tangela": { "zh-TW": "蔓藤怪", "zh-CN": "蔓藤怪", ja: "モンジャラ" },
  "Gengar VMAX": { "zh-TW": "耿鬼VMAX", "zh-CN": "耿鬼VMAX", ja: "ゲンガーVMAX" },
  "Daisy's Care": { "zh-TW": "小霞的照顧", "zh-CN": "小霞的照顾", ja: "カスミのケア" },
  "Pikachu": { "zh-TW": "皮卡丘", "zh-CN": "皮卡丘", ja: "ピカチュウ" },
  "Poncho": { "zh-TW": "斗篷皮卡丘", "zh-CN": "斗篷皮卡丘", ja: "ポンチョを着たピカチュウ" },
  // 085/SVP 只出過英文版（梵高美術館＋海外 Pokémon Center 限定），冇日文印刷版，
  // 所以冇「官方日文卡名」。日本市場一律叫「ゴッホピカチュウ」——magi、スニーカーダンク、
  // ARTnews JAPAN、Hypebeast JP 都用呢個名。原本嗰個「グレーのフェルト帽をかぶった
  // ピカチュウ」係照英文卡名逐字譯出嚟，日本人唔會咁叫。
  "Pikachu with Grey Felt Hat": { "zh-TW": "梵高皮卡丘", "zh-CN": "梵高皮卡丘", ja: "ゴッホピカチュウ", ko: "반 고흐 피카츄" },
  "Mario Pikachu": { "zh-TW": "瑪利歐皮卡丘", "zh-CN": "马力欧皮卡丘", ja: "マリオピカチュウ" },
  "Luigi Pikachu": { "zh-TW": "路易吉皮卡丘", "zh-CN": "路易吉皮卡丘", ja: "ルイージピカチュウ" },
  "Detective Pikachu": { "zh-TW": "名偵探皮卡丘", "zh-CN": "大侦探皮卡丘", ja: "名探偵ピカチュウ" },
  "Cosplay Pikachu": { "zh-TW": "換裝皮卡丘", "zh-CN": "换装皮卡丘", ja: "おきがえピカチュウ" },
  "Pikachu Munch": { "zh-TW": "吶喊皮卡丘", "zh-CN": "呐喊皮卡丘", ja: "ムンク ピカチュウ" },
  "Shibuya Pikachu": { "zh-TW": "澀谷皮卡丘", "zh-CN": "涩谷皮卡丘", ja: "シブヤのピカチュウ" },
  "Mega Tokyo Pikachu": { "zh-TW": "超級東京皮卡丘", "zh-CN": "超级东京皮卡丘", ja: "メガトウキョーのピカチュウ" },
  "Tokyo Pikachu": { "zh-TW": "東京皮卡丘", "zh-CN": "东京皮卡丘", ja: "トウキョーのピカチュウ" },
  "Tohoku Pikachu": { "zh-TW": "東北皮卡丘", "zh-CN": "东北皮卡丘", ja: "トウホクのピカチュウ" },
  "Easter's Pikachu": { "zh-TW": "復活節皮卡丘", "zh-CN": "复活节皮卡丘", ja: "イースターのピカチュウ" },
  "Red's Pikachu": { "zh-TW": "赤紅的皮卡丘", "zh-CN": "赤红的皮卡丘", ja: "レッドのピカチュウ" },
  "Team Skull Pikachu": { "zh-TW": "骷髏隊皮卡丘", "zh-CN": "骷髅队皮卡丘", ja: "スカル団のピカチュウ" },
  "Gyarados Pretend Pikachu": { "zh-TW": "扮暴鯉龍的皮卡丘", "zh-CN": "扮暴鲤龙的皮卡丘", ja: "ギャラドスごっこピカチュウ" },
  "Magikarp Pretend Pikachu": { "zh-TW": "扮鯉魚王的皮卡丘", "zh-CN": "扮鲤鱼王的皮卡丘", ja: "コイキングごっこピカチュウ" },
  "Pikachu playing in the sea": { "zh-TW": "海邊嬉戲的皮卡丘", "zh-CN": "海边嬉戏的皮卡丘", ja: "海で遊ぶピカチュウ" },
  "Pikachu Seven-Eleven": { "zh-TW": "7-Eleven 皮卡丘", "zh-CN": "7-Eleven 皮卡丘", ja: "セブン-イレブンのピカチュウ" },
  "Cherry Blossom Afro Pikachu": { "zh-TW": "櫻花爆炸頭皮卡丘", "zh-CN": "樱花爆炸头皮卡丘", ja: "サクラアフロのピカチュウ" },
  "Manzai play Pikachu": { "zh-TW": "漫才皮卡丘", "zh-CN": "漫才皮卡丘", ja: "漫才ごっこピカチュウ" },
  "Kanazawa Pikachu With Mark": { "zh-TW": "金澤皮卡丘（有標記）", "zh-CN": "金泽皮卡丘（有标记）", ja: "カナザワのピカチュウ（マークあり）" },
  "Kanazawa Pikachu No Mark S-P": { "zh-TW": "金澤皮卡丘（無標記）S-P", "zh-CN": "金泽皮卡丘（无标记）S-P", ja: "カナザワのピカチュウ（マークなし）S-P" },
  "Flying Pikachu VMAX": { "zh-TW": "飛行皮卡丘VMAX", "zh-CN": "飞行皮卡丘VMAX", ja: "そらをとぶピカチュウVMAX" },
  "Surfing Pikachu V": { "zh-TW": "衝浪皮卡丘V", "zh-CN": "冲浪皮卡丘V", ja: "なみのりピカチュウV" },
  "Surfing Pikachu VMAX": { "zh-TW": "衝浪皮卡丘VMAX", "zh-CN": "冲浪皮卡丘VMAX", ja: "なみのりピカチュウVMAX" },
  "Special Delivery Charizard": { "zh-TW": "特快專遞噴火龍", "zh-CN": "特快专递喷火龙", ja: "スペシャルデリバリー リザードン" },
  "Ancient Mew": { "zh-TW": "古代夢幻", "zh-CN": "古代梦幻", ja: "古代のミュウ" },
  "Armored Mewtwo": { "zh-TW": "裝甲超夢", "zh-CN": "装甲超梦", ja: "アーマードミュウツー" },
  "Erika's Hospitality": { "zh-TW": "莉佳的款待", "zh-CN": "莉佳的款待", ja: "エリカのおもてなし" },
  "Erika's Invitation": { "zh-TW": "莉佳的邀請", "zh-CN": "莉佳的邀请", ja: "エリカの招待" },
  "Green's Exploration": { "zh-TW": "小藍的探索", "zh-CN": "小蓝的探索", ja: "グリーンの戦略" },
  "Lillie's Determination": { "zh-TW": "莉莉艾的決心", "zh-CN": "莉莉艾的决心", ja: "リーリエの決心" },
  "Lillie's Full Force": { "zh-TW": "莉莉艾的全力", "zh-CN": "莉莉艾的全力", ja: "リーリエの全力" },
  "Lisia's Appeal": { "zh-TW": "琉琪亞的呼喚", "zh-CN": "琉琪亚的呼唤", ja: "ルチアのアピール" },
  "Rosa's Encouragement": { "zh-TW": "鳴依的鼓舞", "zh-CN": "鸣依的鼓舞", ja: "メイの励まし" },
  "Friends in Alola": { "zh-TW": "阿羅拉的夥伴", "zh-CN": "阿罗拉的伙伴", ja: "アローラの仲間たち" },
  "Sightseer": { "zh-TW": "觀光客", "zh-CN": "观光客", ja: "かんこうきゃく" },
};

// Word-level lexicon applied left-to-right, longest match first.
const LEXICON: Array<[string, LocalizedName]> = [
  ["Portgas D. Ace", { "zh-TW": "波特卡斯・D・艾斯", "zh-CN": "波特卡斯・D・艾斯", ja: "ポートガス・D・エース" }],
  ["Portgas D Ace", { "zh-TW": "波特卡斯・D・艾斯", "zh-CN": "波特卡斯・D・艾斯", ja: "ポートガス・D・エース" }],
  ["Gol D. Roger", { "zh-TW": "哥爾・D・羅傑", "zh-CN": "哥尔・D・罗杰", ja: "ゴール・D・ロジャー" }],
  ["Marshall D. Teach", { "zh-TW": "馬歇爾・D・汀奇", "zh-CN": "马歇尔・D・蒂奇", ja: "マーシャル・D・ティーチ" }],
  ["Monkey D. Luffy", { "zh-TW": "蒙其・D・魯夫", "zh-CN": "蒙奇・D・路飞", ja: "モンキー・D・ルフィ" }],
  ["Yamato", { "zh-TW": "大和", "zh-CN": "大和", ja: "ヤマト" }],
  ["Buggy", { "zh-TW": "巴基", "zh-CN": "巴基", ja: "バギー" }],
  ["Donquixote Doflamingo", { "zh-TW": "唐吉訶德・多佛朗明哥", "zh-CN": "唐吉诃德・多弗拉门戈", ja: "ドンキホーテ・ドフラミンゴ" }],
  ["Terapagos", { "zh-TW": "太樂巴戈斯", "zh-CN": "太乐巴戈斯", ja: "テラパゴス" }],
  ["Raging Bolt", { "zh-TW": "破空焰", "zh-CN": "破空焰", ja: "ウガツホムラ" }],
  ["Iron Crown", { "zh-TW": "鐵武者", "zh-CN": "铁武者", ja: "テツノカシラ" }],
  ["Urshifu", { "zh-TW": "武道熊師", "zh-CN": "武道熊师", ja: "ウーラオス" }],
  ["Zoroark", { "zh-TW": "索羅亞克", "zh-CN": "索罗亚克", ja: "ゾロアーク" }],
  ["Crobat", { "zh-TW": "叉字蝠", "zh-CN": "叉字蝠", ja: "クロバット" }],
  ["Bulbasaur", { "zh-TW": "妙蛙種子", "zh-CN": "妙蛙种子", ja: "フシギダネ" }],
  ["Ivysaur", { "zh-TW": "妙蛙草", "zh-CN": "妙蛙草", ja: "フシギソウ" }],
  ["Tangela", { "zh-TW": "蔓藤怪", "zh-CN": "蔓藤怪", ja: "モンジャラ" }],
  ["Team Rocket's", { "zh-TW": "火箭隊的", "zh-CN": "火箭队的", ja: "ロケット団の" }],
  ["Team Skull", { "zh-TW": "骷髏隊", "zh-CN": "骷髅队", ja: "スカル団" }],
  ["Team Aqua's", { "zh-TW": "海洋隊的", "zh-CN": "海洋队的", ja: "アクア団の" }],
  ["Team Magma's", { "zh-TW": "熔岩隊的", "zh-CN": "熔岩队的", ja: "マグマ団の" }],
  ["Team Rocket", { "zh-TW": "火箭隊", "zh-CN": "火箭队", ja: "ロケット団" }],
  ["Shadow Rider", { "zh-TW": "騎黑馬的", "zh-CN": "骑黑马的", ja: "こくばじゃ" }],
  ["Cynthia's", { "zh-TW": "竹蘭的", "zh-CN": "竹兰的", ja: "シロナの" }],
  ["Lillie's", { "zh-TW": "莉莉艾的", "zh-CN": "莉莉艾的", ja: "リーリエの" }],
  ["Sabrina's", { "zh-TW": "娜姿的", "zh-CN": "娜姿的", ja: "ナツメの" }],
  ["Ethan's", { "zh-TW": "阿響的", "zh-CN": "阿响的", ja: "ヒビキの" }],
  ["Iono's", { "zh-TW": "奇樹的", "zh-CN": "奇树的", ja: "ナンジャモの" }],
  ["Rosa's", { "zh-TW": "鳴依的", "zh-CN": "鸣依的", ja: "メイの" }],
  ["Red's", { "zh-TW": "小智的", "zh-CN": "小智的", ja: "レッドの" }],
  ["Fukuoka's", { "zh-TW": "福岡的", "zh-CN": "福冈的", ja: "フクオカの" }],
  ["Hiroshima's", { "zh-TW": "廣島的", "zh-CN": "广岛的", ja: "ヒロシマの" }],
  ["Yokohama's", { "zh-TW": "橫濱的", "zh-CN": "横滨的", ja: "ヨコハマの" }],
  ["Tohoku's", { "zh-TW": "東北的", "zh-CN": "东北的", ja: "トウホクの" }],
  ["Roaring Moon", { "zh-TW": "轟鳴月", "zh-CN": "轰鸣月", ja: "トドロクツキ" }],
  ["Moltres Zapdos Articuno", { "zh-TW": "火焰鳥・閃電鳥・急凍鳥", "zh-CN": "火焰鸟・闪电鸟・急冻鸟", ja: "ファイヤー・サンダー・フリーザー" }],
  ["Moltres", { "zh-TW": "火焰鳥", "zh-CN": "火焰鸟", ja: "ファイヤー" }],
  ["Zapdos", { "zh-TW": "閃電鳥", "zh-CN": "闪电鸟", ja: "サンダー" }],
  ["Articuno", { "zh-TW": "急凍鳥", "zh-CN": "急冻鸟", ja: "フリーザー" }],
  ["Solgaleo", { "zh-TW": "索爾迦雷歐", "zh-CN": "索尔迦雷欧", ja: "ソルガレオ" }],
  ["Lunala", { "zh-TW": "露奈雅拉", "zh-CN": "露奈雅拉", ja: "ルナアーラ" }],
  ["Reshiram", { "zh-TW": "萊希拉姆", "zh-CN": "莱希拉姆", ja: "レシラム" }],
  ["Zekrom", { "zh-TW": "捷克羅姆", "zh-CN": "捷克罗姆", ja: "ゼクロム" }],
  ["Charizard", { "zh-TW": "噴火龍", "zh-CN": "喷火龙", ja: "リザードン" }],
  ["Charmeleon", { "zh-TW": "火恐龍", "zh-CN": "火恐龙", ja: "リザード" }],
  ["Charmander", { "zh-TW": "小火龍", "zh-CN": "小火龙", ja: "ヒトカゲ" }],
  ["Venusaur", { "zh-TW": "妙蛙花", "zh-CN": "妙蛙花", ja: "フシギバナ" }],
  ["Blastoise", { "zh-TW": "水箭龜", "zh-CN": "水箭龟", ja: "カメックス" }],
  ["Squirtle", { "zh-TW": "傑尼龜", "zh-CN": "杰尼龟", ja: "ゼニガメ" }],
  ["Alakazam", { "zh-TW": "胡地", "zh-CN": "胡地", ja: "フーディン" }],
  ["Dragonite", { "zh-TW": "快龍", "zh-CN": "快龙", ja: "カイリュー" }],
  ["Mewtwo", { "zh-TW": "超夢", "zh-CN": "超梦", ja: "ミュウツー" }],
  ["Mew", { "zh-TW": "夢幻", "zh-CN": "梦幻", ja: "ミュウ" }],
  ["Lugia", { "zh-TW": "洛奇亞", "zh-CN": "洛奇亚", ja: "ルギア" }],
  ["Ho Oh", { "zh-TW": "鳳王", "zh-CN": "凤王", ja: "ホウオウ" }],
  ["Rayquaza", { "zh-TW": "烈空坐", "zh-CN": "烈空坐", ja: "レックウザ" }],
  ["Giratina", { "zh-TW": "騎拉帝納", "zh-CN": "骑拉帝纳", ja: "ギラティナ" }],
  ["Arceus", { "zh-TW": "阿爾宙斯", "zh-CN": "阿尔宙斯", ja: "アルセウス" }],
  ["Dialga", { "zh-TW": "帝牙盧卡", "zh-CN": "帝牙卢卡", ja: "ディアルガ" }],
  ["Palkia", { "zh-TW": "帕路奇亞", "zh-CN": "帕路奇亚", ja: "パルキア" }],
  ["Darkrai", { "zh-TW": "達克萊伊", "zh-CN": "达克莱伊", ja: "ダークライ" }],
  ["Kyogre", { "zh-TW": "蓋歐卡", "zh-CN": "盖欧卡", ja: "カイオーガ" }],
  ["Groudon", { "zh-TW": "固拉多", "zh-CN": "固拉多", ja: "グラードン" }],
  ["Garchomp", { "zh-TW": "烈咬陸鯊", "zh-CN": "烈咬陆鲨", ja: "ガブリアス" }],
  ["Lucario", { "zh-TW": "路卡利歐", "zh-CN": "路卡利欧", ja: "ルカリオ" }],
  ["Greninja", { "zh-TW": "甲賀忍蛙", "zh-CN": "甲贺忍蛙", ja: "ゲッコウガ" }],
  ["Gardevoir", { "zh-TW": "沙奈朵", "zh-CN": "沙奈朵", ja: "サーナイト" }],
  ["Gengar", { "zh-TW": "耿鬼", "zh-CN": "耿鬼", ja: "ゲンガー" }],
  ["Haunter", { "zh-TW": "鬼斯通", "zh-CN": "鬼斯通", ja: "ゴースト" }],
  ["Mimikyu", { "zh-TW": "謎擬Ｑ", "zh-CN": "谜拟Ｑ", ja: "ミミッキュ" }],
  ["Magikarp", { "zh-TW": "鯉魚王", "zh-CN": "鲤鱼王", ja: "コイキング" }],
  ["Gyarados", { "zh-TW": "暴鯉龍", "zh-CN": "暴鲤龙", ja: "ギャラドス" }],
  ["Eevee", { "zh-TW": "伊布", "zh-CN": "伊布", ja: "イーブイ" }],
  ["Vaporeon", { "zh-TW": "水伊布", "zh-CN": "水伊布", ja: "シャワーズ" }],
  ["Jolteon", { "zh-TW": "雷伊布", "zh-CN": "雷伊布", ja: "サンダース" }],
  ["Flareon", { "zh-TW": "火伊布", "zh-CN": "火伊布", ja: "ブースター" }],
  ["Espeon", { "zh-TW": "太陽伊布", "zh-CN": "太阳伊布", ja: "エーフィ" }],
  ["Umbreon", { "zh-TW": "月亮伊布", "zh-CN": "月亮伊布", ja: "ブラッキー" }],
  ["Leafeon", { "zh-TW": "葉伊布", "zh-CN": "叶伊布", ja: "リーフィア" }],
  ["Glaceon", { "zh-TW": "冰伊布", "zh-CN": "冰伊布", ja: "グレイシア" }],
  ["Sylveon", { "zh-TW": "仙子伊布", "zh-CN": "仙子伊布", ja: "ニンフィア" }],
  ["Latias", { "zh-TW": "拉帝亞斯", "zh-CN": "拉帝亚斯", ja: "ラティアス" }],
  ["Latios", { "zh-TW": "拉帝歐斯", "zh-CN": "拉帝欧斯", ja: "ラティオス" }],
  ["Psyduck", { "zh-TW": "可達鴨", "zh-CN": "可达鸭", ja: "コダック" }],
  ["Meowth", { "zh-TW": "喵喵", "zh-CN": "喵喵", ja: "ニャース" }],
  ["Snorlax", { "zh-TW": "卡比獸", "zh-CN": "卡比兽", ja: "カビゴン" }],
  ["Cramorant", { "zh-TW": "古月鳥", "zh-CN": "古月鸟", ja: "ウッウ" }],
  ["Wailord", { "zh-TW": "吼鯨王", "zh-CN": "吼鲸王", ja: "ホエルオー" }],
  ["Kingdra", { "zh-TW": "刺龍王", "zh-CN": "刺龙王", ja: "キングドラ" }],
  ["Clefairy", { "zh-TW": "皮皮", "zh-CN": "皮皮", ja: "ピッピ" }],
  ["Oricorio", { "zh-TW": "花舞鳥", "zh-CN": "花舞鸟", ja: "オドリドリ" }],
  ["Rowlet", { "zh-TW": "木木梟", "zh-CN": "木木枭", ja: "モクロー" }],
  ["Wattrel", { "zh-TW": "電海燕", "zh-CN": "电海燕", ja: "カイデン" }],
  ["Calyrex", { "zh-TW": "蕾冠王", "zh-CN": "蕾冠王", ja: "バドレックス" }],
  ["Victini", { "zh-TW": "比克提尼", "zh-CN": "比克提尼", ja: "ビクティニ" }],
  ["Zygarde", { "zh-TW": "基格爾德", "zh-CN": "基格尔德", ja: "ジガルデ" }],
  ["Hoopa", { "zh-TW": "胡帕", "zh-CN": "胡帕", ja: "フーパ" }],
  ["Pikachu", { "zh-TW": "皮卡丘", "zh-CN": "皮卡丘", ja: "ピカチュウ" }],
  ["Monkey D Luffy", { "zh-TW": "蒙其・D・魯夫", "zh-CN": "蒙奇・D・路飞", ja: "モンキー・D・ルフィ" }],
  ["Monkey.D.Luffy", { "zh-TW": "蒙其・D・魯夫", "zh-CN": "蒙奇・D・路飞", ja: "モンキー・D・ルフィ" }],
  ["Roronoa Zoro", { "zh-TW": "羅羅亞・索隆", "zh-CN": "罗罗诺亚・索隆", ja: "ロロノア・ゾロ" }],
  ["Nami", { "zh-TW": "娜美", "zh-CN": "娜美", ja: "ナミ" }],
  ["Sanji", { "zh-TW": "香吉士", "zh-CN": "山智", ja: "サンジ" }],
  ["Tony Tony Chopper", { "zh-TW": "多尼多尼・喬巴", "zh-CN": "托尼托尼・乔巴", ja: "トニートニー・チョッパー" }],
  ["Boa Hancock", { "zh-TW": "波雅・漢考克", "zh-CN": "波雅・汉库克", ja: "ボア・ハンコック" }],
  ["Trafalgar Law", { "zh-TW": "托拉法爾加・羅", "zh-CN": "特拉法尔加・罗", ja: "トラファルガー・ロー" }],
  ["Dracule Mihawk", { "zh-TW": "喬拉可爾・密佛格", "zh-CN": "乔拉可尔・米霍克", ja: "ジュラキュール・ミホーク" }],
  ["Marshall D Teach", { "zh-TW": "馬歇爾・D・汀奇", "zh-CN": "马歇尔・D・蒂奇", ja: "マーシャル・D・ティーチ" }],
  ["Gol D Roger", { "zh-TW": "哥爾・D・羅傑", "zh-CN": "哥尔・D・罗杰", ja: "ゴール・D・ロジャー" }],
  ["Shanks", { "zh-TW": "香克斯", "zh-CN": "香克斯", ja: "シャンクス" }],
  ["Sabo", { "zh-TW": "薩波", "zh-CN": "萨博", ja: "サボ" }],
  ["Lillie", { "zh-TW": "莉莉艾", "zh-CN": "莉莉艾", ja: "リーリエ" }],
  ["Marnie", { "zh-TW": "瑪俐", "zh-CN": "玛俐", ja: "マリィ" }],
  ["Iono", { "zh-TW": "奇樹", "zh-CN": "奇树", ja: "ナンジャモ" }],
  ["Miriam", { "zh-TW": "米莫莎", "zh-CN": "米莫莎", ja: "ミモザ" }],
  ["Carmine", { "zh-TW": "丹瑜", "zh-CN": "丹瑜", ja: "セイリー" }],
  ["Rosa", { "zh-TW": "鳴依", "zh-CN": "鸣依", ja: "メイ" }],
  ["Mega", { "zh-TW": "超級", "zh-CN": "超级", ja: "メガ" }],
  ["Radiant", { "zh-TW": "光輝", "zh-CN": "光辉", ja: "かがやく" }],
  ["Shining", { "zh-TW": "閃耀", "zh-CN": "闪耀", ja: "ひかる" }],
  ["Dark", { "zh-TW": "黑暗", "zh-CN": "黑暗", ja: "ダーク" }],
];

const SUFFIXES: Array<[RegExp, string]> = [
  [/^(ex|exs)$/i, "ex"],
  [/^vmax$/i, "VMAX"],
  [/^vstar$/i, "VSTAR"],
  [/^v$/i, "V"],
  [/^gx$/i, "GX"],
  [/^x$/i, "X"],
  [/^star$/i, "★"],
];

// Tokens that keep their exact English form (rarity codes, promo/set jargon).
const SKIP_TOKENS = new Set([
  "l", "m", "p", "d", "sr", "sr-p", "sr-sp", "sr-spc", "sec", "sec-p", "sec-sp", "sec-spc",
  "sec-gsp", "r-sp", "r-spc", "uc-tr", "sir", "ir", "sar", "ar", "ur", "sur", "hr", "pr", "rr", "rrr", "s-p", "promo",
  "special", "card", "cards", "pack", "booster", "premium", "with", "mark", "no", "years",
  "3th", "3rd", "vol.2", "vol.1", "the", "best", "of", "a", "an", "in", "on", "wearing",
  "album", "set", "to", "commemorate", "release", "movie", "coming!", "one", "piece",
]);

// Exact whole-name overrides for tag-team combos and multi-word specials that
// the word lexicon would otherwise render in an awkward order.
const COMPOSED_EXACT: Record<string, LocalizedName> = {
  "Pikachu & Zekrom GX": { "zh-TW": "皮卡丘＆捷克羅姆GX", "zh-CN": "皮卡丘＆捷克罗姆GX", ja: "ピカチュウ＆ゼクロムGX", ko: "피카츄&제크로무GX" },
  "Pikachu/Zekrom Gx": { "zh-TW": "皮卡丘＆捷克羅姆GX", "zh-CN": "皮卡丘＆捷克罗姆GX", ja: "ピカチュウ＆ゼクロムGX", ko: "피카츄&제크로무GX" },
  "Latias & Latios GX": { "zh-TW": "拉帝亞斯＆拉帝歐斯GX", "zh-CN": "拉帝亚斯＆拉帝欧斯GX", ja: "ラティアス＆ラティオスGX", ko: "라티아스&라티오스GX" },
  "Latias/Latios Gx": { "zh-TW": "拉帝亞斯＆拉帝歐斯GX", "zh-CN": "拉帝亚斯＆拉帝欧斯GX", ja: "ラティアス＆ラティオスGX", ko: "라티아스&라티오스GX" },
  "Gengar & Mimikyu GX": { "zh-TW": "耿鬼＆謎擬ＱGX", "zh-CN": "耿鬼＆谜拟ＱGX", ja: "ゲンガー＆ミミッキュGX", ko: "팬텀&따라큐GX" },
  "Gengar/Mimikyu Gx": { "zh-TW": "耿鬼＆謎擬ＱGX", "zh-CN": "耿鬼＆谜拟ＱGX", ja: "ゲンガー＆ミミッキュGX", ko: "팬텀&따라큐GX" },
  "Mewtwo & Mew GX": { "zh-TW": "超夢＆夢幻GX", "zh-CN": "超梦＆梦幻GX", ja: "ミュウツー＆ミュウGX", ko: "뮤츠&뮤GX" },
  "Umbreon & Darkrai GX": { "zh-TW": "月亮伊布＆達克萊伊GX", "zh-CN": "月亮伊布＆达克莱伊GX", ja: "ブラッキー＆ダークライGX", ko: "블래키&다크라이GX" },
  "Reshiram & Charizard GX": { "zh-TW": "萊希拉姆＆噴火龍GX", "zh-CN": "莱希拉姆＆喷火龙GX", ja: "レシラム＆リザードンGX", ko: "레시라무&리자몽GX" },
  "Reshiram & Zekrom GX": { "zh-TW": "萊希拉姆＆捷克羅姆GX", "zh-CN": "莱希拉姆＆捷克罗姆GX", ja: "レシラム＆ゼクロムGX", ko: "레시라무&제크로무GX" },
  "Solgaleo & Lunala GX": { "zh-TW": "索爾迦雷歐＆露奈雅拉GX", "zh-CN": "索尔迦雷欧＆露奈雅拉GX", ja: "ソルガレオ＆ルナアーラGX", ko: "솔가레오&루나아라GX" },
  "Gardevoir & Sylveon GX": { "zh-TW": "沙奈朵＆仙子伊布GX", "zh-CN": "沙奈朵＆仙子伊布GX", ja: "サーナイト＆ニンフィアGX", ko: "가디안&님피아GX" },
  "Eevee & Snorlax GX": { "zh-TW": "伊布＆卡比獸GX", "zh-CN": "伊布＆卡比兽GX", ja: "イーブイ＆カビゴンGX", ko: "이브이&잠만보GX" },
  "Magikarp & Wailord GX": { "zh-TW": "鯉魚王＆吼鯨王GX", "zh-CN": "鲤鱼王＆吼鲸王GX", ja: "コイキング＆ホエルオーGX", ko: "잉어킹&고래왕GX" },
  "Magikarp/Wailord Gx": { "zh-TW": "鯉魚王＆吼鯨王GX", "zh-CN": "鲤鱼王＆吼鲸王GX", ja: "コイキング＆ホエルオーGX", ko: "잉어킹&고래왕GX" },
  "Arceus & Dialga & Palkia GX": { "zh-TW": "阿爾宙斯＆帝牙盧卡＆帕路奇亞GX", "zh-CN": "阿尔宙斯＆帝牙卢卡＆帕路奇亚GX", ja: "アルセウス＆ディアルガ＆パルキアGX", ko: "아르세우스&디아루가&펄기아GX" },
  "M Charizard EX": { "zh-TW": "超級噴火龍ex", "zh-CN": "超级喷火龙ex", ja: "メガリザードンex", ko: "메가리자몽ex" },
  "M Charizard Ex": { "zh-TW": "超級噴火龍ex", "zh-CN": "超级喷火龙ex", ja: "メガリザードンex", ko: "메가리자몽ex" },
  "M Rayquaza EX": { "zh-TW": "超級烈空坐ex", "zh-CN": "超级烈空坐ex", ja: "メガレックウザex", ko: "메가레쿠쟈ex" },
  "M Gengar EX": { "zh-TW": "超級耿鬼ex", "zh-CN": "超级耿鬼ex", ja: "メガゲンガーex", ko: "메가팬텀ex" },
  "Rocket's Mewtwo": { "zh-TW": "火箭隊的超夢", "zh-CN": "火箭队的超梦", ja: "ロケット団のミュウツー", ko: "로켓단의 뮤츠" },
  "Ethan's Ho-Oh ex": { "zh-TW": "阿響的鳳王ex", "zh-CN": "阿响的凤王ex", ja: "ヒビキのホウオウex", ko: "심향의 칠색조ex" },
  "_____'s Pikachu": { "zh-TW": "皮卡丘", "zh-CN": "皮卡丘", ja: "ピカチュウ", ko: "피카츄" },
  "Pikachu wearing a poncho": { "zh-TW": "穿斗篷的皮卡丘", "zh-CN": "穿斗篷的皮卡丘", ja: "ポンチョを着たピカチュウ", ko: "판초를 입은 피카츄" },
  "Pikachu wearing a poncho Pikachu": { "zh-TW": "穿斗篷的皮卡丘", "zh-CN": "穿斗篷的皮卡丘", ja: "ポンチョを着たピカチュウ", ko: "판초를 입은 피카츄" },
  "Rowlet Munch": { "zh-TW": "吶喊木木梟", "zh-CN": "呐喊木木枭", ja: "ムンク モクロー", ko: "뭉크 나몰빼미" },
  "Charizard VMAX SUR": { "zh-TW": "噴火龍VMAX SUR", "zh-CN": "喷火龙VMAX SUR", ja: "リザードンVMAX SUR", ko: "리자몽VMAX SUR" },
  "Monkey.D.Luffy SEC-SP Booster Pack Awakening Of The New Era": { "zh-TW": "蒙其・D・魯夫SEC-SP", "zh-CN": "蒙奇・D・路飞SEC-SP", ja: "モンキー・D・ルフィSEC-SP", ko: "몽키・D・루피SEC-SP" },
  "Monkey.D.Luffy SEC-SP Booster Pack Emperors In The New World": { "zh-TW": "蒙其・D・魯夫SEC-SP", "zh-CN": "蒙奇・D・路飞SEC-SP", ja: "モンキー・D・ルフィSEC-SP", ko: "몽키・D・루피SEC-SP" },
  "Monkey.D.Luffy SEC-SP Booster Pack CARRYING ON HIS WILL": { "zh-TW": "蒙其・D・魯夫SEC-SP", "zh-CN": "蒙奇・D・路飞SEC-SP", ja: "モンキー・D・ルフィSEC-SP", ko: "몽키・D・루피SEC-SP" },
  "Monkey.D.Luffy SEC-SP Booster Pack A Fist of Divine Speed": { "zh-TW": "蒙其・D・魯夫SEC-SP", "zh-CN": "蒙奇・D・路飞SEC-SP", ja: "モンキー・D・ルフィSEC-SP", ko: "몽키・D・루피SEC-SP" },
  "Monkey.D.Luffy SEC-SPC 3th Anniversary Special Card Booster Pack A Fist of Divine Speed": { "zh-TW": "蒙其・D・魯夫SEC-SPC", "zh-CN": "蒙奇・D・路飞SEC-SPC", ja: "モンキー・D・ルフィSEC-SPC", ko: "몽키・D・루피SEC-SPC" },
  "Roronoa Zoro SEC-SP Booster Pack Wings Of The Captain": { "zh-TW": "羅羅亞・索隆SEC-SP", "zh-CN": "罗罗诺亚・索隆SEC-SP", ja: "ロロノア・ゾロSEC-SP", ko: "롤로노아・조로SEC-SP" },
  "Nami R-SP Premium Booster One Piece Card The Best": { "zh-TW": "娜美R-SP", "zh-CN": "娜美R-SP", ja: "ナミR-SP", ko: "나미R-SP" },
  "Boa Hancock SR-SP Booster Pack The Future After 500 years": { "zh-TW": "波雅・漢考克SR-SP", "zh-CN": "波雅・汉库克SR-SP", ja: "ボア・ハンコックSR-SP", ko: "보아・핸콕SR-SP" },
  "Shanks SEC-SP Booster Pack ROMANCE DAWN": { "zh-TW": "香克斯SEC-SP", "zh-CN": "香克斯SEC-SP", ja: "シャンクスSEC-SP", ko: "샹크스SEC-SP" },
  "Sanji SEC-SP Premium Booster One Piece Card The Best vol.2": { "zh-TW": "香吉士SEC-SP", "zh-CN": "山智SEC-SP", ja: "サンジSEC-SP", ko: "상디SEC-SP" },
  "Marshall.D.Teach SR-SP Booster Pack Emperors In The New World": { "zh-TW": "馬歇爾・D・汀奇SR-SP", "zh-CN": "马歇尔・D・蒂奇SR-SP", ja: "マーシャル・D・ティーチSR-SP", ko: "마샬・D・티치SR-SP" },
  "Trafalgar Law SEC-SP Booster Pack Royal Blood": { "zh-TW": "托拉法爾加・羅SEC-SP", "zh-CN": "特拉法尔加・罗SEC-SP", ja: "トラファルガー・ローSEC-SP", ko: "트라팔가・로SEC-SP" },
  "Sabo SEC-SP Booster Pack CARRYING ON HIS WILL": { "zh-TW": "薩波SEC-SP", "zh-CN": "萨博SEC-SP", ja: "サボSEC-SP", ko: "사보SEC-SP" },
  "Dracule Mihawk SEC-SP Booster Pack THE AZURE SEA'S SEVEN": { "zh-TW": "喬拉可爾・密佛格SEC-SP", "zh-CN": "乔拉可尔・米霍克SEC-SP", ja: "ジュラキュール・ミホークSEC-SP", ko: "쥬라큘・미호크SEC-SP" },
  "Hoopa Promotional Cards Hoopa's Coming! Album Set to Commemorate the Release of the Movie": { "zh-TW": "胡帕（光輪的超魔神）", "zh-CN": "胡帕（光轮的超魔神）", ja: "フーパ（光輪の超魔神）", ko: "후파(광륜의 초마신)" },
};

const lexicon = [...LEXICON].sort((a, b) => b[0].length - a[0].length);
const exact = new Map(Object.entries(EXACT).map(([key, value]) => [normalizeKey(key), value]));
const composedExact = new Map(Object.entries(COMPOSED_EXACT).map(([key, value]) => [normalizeKey(key), value]));

function normalizeKey(name: string): string {
  return name.trim().replace(/[.,]/g, "").replace(/\s+/g, " ").toLowerCase();
}

const KO_LEXICON: Array<[string, string]> = [
// board residual species / trainers / OP names
  ["Nidoking", "니드킹"],
  ["Sharpedo", "샤크니아"],
  ["Bellibolt", "찌리배리"],
  ["Giovanni", "비주기"],
  ["Wartortle", "어니부기"],
  ["Jewelry Bonney", "쥬얼리 보니"],
  ["Zoro-Juurou", "조로주로"],
  ["Poliwhirl", "슈륙챙이"],
  ["Persian", "페르시온"],
  ["Ceruledge", "파라블레이즈"],
  ["Machoke", "근육몬"],
  ["Dragonair", "신뇽"],
  ["Hydreigon", "삼삼드래"],
  ["Alolan Exeggutor", "알로라 나시"],
  ["Alolan", "알로라"],
  ["Exeggutor", "나시"],
  ["Enel", "에넬"],
  ["Bloodmoon Ursaluna", "블러드문 우라오스나"],
  ["Ursaluna", "우라오스나"],
  ["Bloodmoon", "블러드문"],
  ["Giovanni's Charisma", "비주기의 카리스마"],
  ["Giovanni's", "비주기의"],
  ["Divine Departure", "신속 이탈"],
  ["Mr. Mime", "마임맨"],
  ["Mr Mime", "마임맨"],
  ["Caterpie", "캐터피"],
  ["Iron Valiant", "무쇠무인"],
  ["Valiant", "무인"],
  ["Nico Robin", "니코 로빈"],
  ["Gholdengo", "타부자고"],
  ["Omanyte", "암나이트"],
  ["Kieran", "카지"],
  ["Lacey", "타로"],
  ["Suicune", "스이쿤"],
  ["Celebi", "세레비"],
  ["Entei", "앤테이"],
  ["Aerodactyl", "프테라"],
  ["Gear Two", "기어 세컨드"],
  ["Deoxys", "테오키스"],
  ["Raikou", "라이코"],
  ["Milotic", "밀로틱"],
  ["Luffy-Tarou", "루피타로"],
  ["Silvers Rayleigh", "실버즈 레일리"],
  ["Rayleigh", "레일리"],
  ["Silvers", "실버즈"],
  ["Eustass \"Captain\" Kid", "유스타스 캡틴 키드"],
  ["Eustass Captain Kid", "유스타스 캡틴 키드"],
  ["Eustass", "유스타스"],
  ["Kid", "키드"],
  ["Salamence", "보만다"],
  ["Marshadow", "마샤도"],
  ["Honchkrow", "돈크로우"],
  ["Lumineon", "네오라이트"],
  ["Galarian", "가라르"],
  ["Origin Forme", "오리진폼"],
  ["Origin", "오리진"],
  ["Forme", "폼"],
  ["Rapid Strike", "연격"],
  ["Single Strike", "일격"],
  ["Portgas.D.Ace", "포트거스・D・에이스"],
  ["Portgas D Ace", "포트거스・D・에이스"],
  ["Monkey.D.Luffy", "몽키・D・루피"],
  ["Rebecca", "레베카"],
  ["O-Nami", "오나미"],
  ["Ms. All Sunday", "미스 올 선데이"],
  ["Ms All Sunday", "미스 올 선데이"],
  ["N's", "N의"],
  ["Daisy's Care", "이슬의 케어"],
  ["Daisy's", "이슬의"],
  ["Acerola's Premonition", "에이리카의 예감"],
  ["Acerola's", "에이리카의"],
  ["Marnie's Pride", "마리의 긍지"],
  ["Marnie's", "마리의"],
  // print descriptors
  ["Alternate Art Secret", "얼터네이트 아트 시크릿"],
  ["Alternate Full Art", "얼터네이트 풀아트"],
  ["Alternate Art", "얼터네이트 아트"],
  ["Full Art", "풀아트"],
  ["Secret", "시크릿"],
  ["Errata", "에라타"],
  ["Special Art Rare", "스페셜 아트 레어"],
  ["Manga Alternate Art", "만화 얼터네이트 아트"],
  ["Comic Parallel", "코믹 패러렐"],
  ["Portgas D. Ace", "포트거스・D・에이스"],
  ["Gol D. Roger", "골・D・로저"],
  ["Marshall D. Teach", "마샬・D・티치"],
  ["Monkey D. Luffy", "몽키・D・루피"],
  ["Yamato", "야마토"],
  ["Buggy", "버기"],
  ["Donquixote Doflamingo", "돈키호테・도플라밍고"],
  ["Terapagos", "테라파고스"],
  ["Raging Bolt", "우가츠호무라"],
  ["Iron Crown", "테츠노카시라"],
  ["Urshifu", "우라오스"],
  ["Zoroark", "조로아크"],
  ["Crobat", "크로뱃"],
  ["Bulbasaur", "이상해씨"],
  ["Ivysaur", "이상해풀"],
  ["Tangela", "덩구리"],
  ["Team Rocket's", "로켓단의"], ["Team Rocket", "로켓단"], ["Team Skull", "스컬단"],
  ["Team Aqua's", "아쿠아단의"], ["Team Magma's", "마그마단의"], ["Shadow Rider", "흑마 탄"],
  ["Cynthia's", "난천의"], ["Lillie's", "릴리에의"], ["Sabrina's", "초련의"], ["Ethan's", "심향의"],
  ["Iono's", "모야모의"], ["Rosa's", "명희의"], ["Red's", "레드의"], ["Fukuoka's", "후쿠오카의"],
  ["Hiroshima's", "히로시마의"], ["Yokohama's", "요코하마의"], ["Tohoku's", "도호쿠의"],
  ["Roaring Moon", "고동치는달"], ["Moltres Zapdos Articuno", "파이어・썬더・프리져"],
  ["Moltres", "파이어"], ["Zapdos", "썬더"], ["Articuno", "프리져"],
  ["Solgaleo", "솔가레오"], ["Lunala", "루나아라"], ["Reshiram", "레시라무"], ["Zekrom", "제크로무"],
  ["Charizard", "리자몽"], ["Charmeleon", "리자드"], ["Charmander", "파이리"], ["Venusaur", "이상해꽃"],
  ["Blastoise", "거북왕"], ["Squirtle", "꼬부기"], ["Alakazam", "후딘"], ["Dragonite", "망나뇽"],
  ["Mewtwo", "뮤츠"], ["Mew", "뮤"], ["Lugia", "루기아"], ["Ho Oh", "칠색조"], ["Rayquaza", "레쿠쟈"],
  ["Giratina", "기라티나"], ["Arceus", "아르세우스"], ["Dialga", "디아루가"], ["Palkia", "펄기아"],
  ["Darkrai", "다크라이"], ["Kyogre", "가이오가"], ["Groudon", "그란돈"], ["Garchomp", "한카리아스"],
  ["Lucario", "루카리오"], ["Greninja", "개굴닌자"], ["Gardevoir", "가디안"], ["Gengar", "팬텀"],
  ["Haunter", "고우스트"], ["Mimikyu", "따라큐"], ["Magikarp", "잉어킹"], ["Gyarados", "갸라도스"],
  ["Eevee", "이브이"], ["Vaporeon", "샤미드"], ["Jolteon", "쥬피썬더"], ["Flareon", "부스터"],
  ["Espeon", "에브이"], ["Umbreon", "블래키"], ["Leafeon", "리피아"], ["Glaceon", "글레이시아"],
  ["Sylveon", "님피아"], ["Latias", "라티아스"], ["Latios", "라티오스"], ["Psyduck", "고라파덕"],
  ["Meowth", "나옹"], ["Snorlax", "잠만보"], ["Cramorant", "윽우지"], ["Wailord", "고래왕"],
  ["Kingdra", "킹드라"], ["Clefairy", "삐삐"], ["Oricorio", "춤추새"], ["Rowlet", "나몰빼미"],
  ["Wattrel", "찌리비"], ["Calyrex", "버드렉스"], ["Victini", "비크티니"], ["Zygarde", "지가륍데"],
  ["Hoopa", "후파"], ["Pikachu", "피카츄"],
  ["Monkey D Luffy", "몽키・D・루피"],
  ["Roronoa Zoro", "롤로노아・조로"], ["Nami", "나미"], ["Sanji", "상디"],
  ["Tony Tony Chopper", "토니토니・쵸파"], ["Boa Hancock", "보아・핸콕"], ["Trafalgar Law", "트라팔가・로"],
  ["Dracule Mihawk", "쥬라큘・미호크"], ["Marshall D Teach", "마샬・D・티치"], ["Gol D Roger", "골・D・로저"],
  ["Shanks", "샹크스"], ["Sabo", "사보"],
  ["Lillie", "릴리에"], ["Marnie", "마리"], ["Iono", "모야모"], ["Miriam", "미모사"],
  ["Carmine", "시유"], ["Rosa", "명희"],
  ["Mega", "메가"], ["Radiant", "빛나는"], ["Shining", "빛나는"], ["Dark", "다크"],
];
const koLexicon = new Map(KO_LEXICON.map(([source, target]) => [normalizeKey(source), target]));

const KO_EXACT: Record<string, string> = {
  "demon aura nine sword style asura blades drawn dead mans game": "아수라 구도류 염라 발도",
  "don!! card (gold)": "DON!! 카드 (골드)",
  "don!! card gold": "DON!! 카드 (골드)",
  "portgas.d.ace sec-sp booster pack carrying on his will": "포트거스・D・에이스 SEC-SP",
  "silvers rayleigh sec-sp booster pack two legends": "실버즈 레일리 SEC-SP",
  "monkey.d.luffy uc-tr": "몽키・D・루피 UC-TR",
  "roronoa zoro r-spc": "롤로노아・조로 R-SPC",
  "glaceon vstar sir": "글레이시아VSTAR SIR",
  "meowth ir": "나옹 IR",
  "poncho": "판초를 입은 피카츄",
  "pikachu with grey felt hat": "반 고흐 피카츄",
  "mario pikachu": "마리오 피카츄",
  "luigi pikachu": "루이지 피카츄",
  "detective pikachu": "명탐정 피카츄",
  "cosplay pikachu": "코스프레 피카츄",
  "pikachu munch": "뭉크 피카츄",
  "shibuya pikachu": "시부야 피카츄",
  "mega tokyo pikachu": "메가 도쿄 피카츄",
  "tokyo pikachu": "도쿄 피카츄",
  "tohoku pikachu": "도호쿠 피카츄",
  "easter's pikachu": "이스터 피카츄",
  "red's pikachu": "레드의 피카츄",
  "team skull pikachu": "스컬단 피카츄",
  "gyarados pretend pikachu": "갸라도스 흉내 피카츄",
  "magikarp pretend pikachu": "잉어킹 흉내 피카츄",
  "pikachu playing in the sea": "바다에서 노는 피카츄",
  "pikachu seven-eleven": "세븐일레븐 피카츄",
  "cherry blossom afro pikachu": "벚꽃 아프로 피카츄",
  "manzai play pikachu": "만담 피카츄",
  "kanazawa pikachu with mark": "가나자와 피카츄(마크 있음)",
  "kanazawa pikachu no mark s-p": "가나자와 피카츄(마크 없음) S-P",
  "flying pikachu vmax": "하늘을 나는 피카츄VMAX",
  "surfing pikachu v": "파도타기 피카츄V",
  "surfing pikachu vmax": "파도타기 피카츄VMAX",
  "special delivery charizard": "스페셜 딜리버리 리자몽",
  "ancient mew": "고대의 뮤",
  "armored mewtwo": "아머드 뮤츠",
  "erika's hospitality": "민화의 환대",
  "erika's invitation": "민화의 초대",
  "green's exploration": "그린의 탐색",
  "lillie's determination": "릴리에의 결심",
  "lillie's full force": "릴리에의 전력",
  "lisia's appeal": "루치아의 어필",
  "rosa's encouragement": "명희의 격려",
  "friends in alola": "알로라의 친구들",
  "sightseer": "관광객",
};
const koExact = new Map(Object.entries(KO_EXACT).map(([key, value]) => [normalizeKey(key), value]));

function isCjk(locale: Locale): boolean {
  return locale !== "en";
}

function translateTokens(name: string, locale: Locale): string | null {
  const normalized = name.replace(/[\/&]/g, " & ").replace(/[()]/g, " ").replace(/(?<=[A-Za-z])\.(?=[A-Za-z])/g, " ");
  const tokens = normalized.split(/\s+/).filter(Boolean);
  const out: string[] = [];
  const englishTail: string[] = [];
  let index = 0;
  let translated = false;
  while (index < tokens.length) {
    let matched = false;
    for (let span = Math.min(4, tokens.length - index); span > 0; span--) {
      const phrase = tokens.slice(index, index + span).join(" ");
      const entry = lexicon.find(([source]) => source.toLowerCase() === normalizeKey(phrase));
      if (entry?.[1][locale]) {
        out.push(entry[1][locale]!);
        index += span;
        matched = true;
        translated = true;
        break;
      }
      if (span === 1) {
        const clean = phrase.replace(/[.,]/g, "");
        for (const [pattern, replacement] of SUFFIXES) {
          if (pattern.test(clean)) {
            out.push(replacement);
            index += 1;
            matched = true;
            break;
          }
        }
        if (!matched && (SKIP_TOKENS.has(clean.toLowerCase()) || /^\d+$/.test(clean))) {
          out.push(clean);
          index += 1;
          matched = true;
        }
        if (!matched && translated) {
          // Best effort: once a recognisable card name was found, keep an
          // untranslatable set/booster tail in English instead of dropping
          // the whole card to the English fallback.
          englishTail.push(phrase);
          index += 1;
          matched = true;
        }
        if (matched) break;
      }
    }
    if (!matched) return null;
  }
  const joined = out.join(isCjk(locale) ? "" : " ");
  if (!joined) return null;
  return englishTail.length ? `${joined} ${englishTail.join(" ")}` : joined;
}

export function localizedCardName(englishName: string, current: string | null | undefined, locale: Locale): string {
  if (current) return current;
  if (locale === "en") return englishName;
  const key = normalizeKey(englishName);
  if (locale === "ko") {
    const koHit = koExact.get(key) ?? composedExact.get(key)?.ko;
    if (koHit) return koHit;
    const tokens = englishName.trim().replace(/[\/&]/g, " & ").replace(/(?<=[A-Za-z])\.(?=[A-Za-z])/g, " ").split(/\s+/).filter(Boolean);
    const out: string[] = [];
    const englishTail: string[] = [];
    let index = 0;
    let translated = false;
    while (index < tokens.length) {
      let matched = false;
      for (let span = Math.min(4, tokens.length - index); span > 0; span--) {
        const phrase = tokens.slice(index, index + span).join(" ");
        const koWord = koLexicon.get(normalizeKey(phrase));
        if (koWord) {
          out.push(koWord);
          index += span;
          matched = true;
          translated = true;
          break;
        }
        if (span === 1) {
          const clean = phrase.replace(/[.,]/g, "");
          let done = false;
          for (const [pattern, replacement] of SUFFIXES) {
            if (pattern.test(clean)) { out.push(replacement); index += 1; done = true; break; }
          }
          if (!done && (SKIP_TOKENS.has(clean.toLowerCase()) || /^\d+$/.test(clean))) {
            out.push(clean);
            index += 1;
            done = true;
          }
          if (!done && translated) {
            englishTail.push(phrase);
            index += 1;
            done = true;
          }
          if (done) { matched = true; break; }
        }
      }
      if (!matched) return englishName;
    }
    const joined = out.join("");
    if (!joined) return englishName;
    return englishTail.length ? `${joined} ${englishTail.join(" ")}` : joined;
  }
  const exactHit = exact.get(key)?.[locale] ?? composedExact.get(key)?.[locale];
  if (exactHit) return exactHit;
  return translateTokens(englishName.trim(), locale) ?? englishName;
}
