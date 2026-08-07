# 2026-08-06 寮€鍙戞棩蹇?
## 宸ヤ綔鎬ц川涓庤寖鍥?
鏈疆缁х画瀹℃煡骞舵牎鍑嗕簩闃剁珷鑺傚伐浣滃彴璁捐锛岃寖鍥村寘鎷洰鏍囨祦绋嬨€丄gent 鏁伴噺銆丩angSmith 瑙傛祴杈圭晫銆乣ChapterWorkflowRead` 璇诲彇濂戠害銆丳lanner 鍐崇瓥閾俱€佸瓧娈垫潵婧愬拰 Canon 娴佺▼銆傞€氳繃鐜版湁婧愮爜閫愰」鏍稿璁″垝涔︼紝淇涓庣湡瀹炲疄鐜颁笉涓€鑷寸殑鎻忚堪锛屽苟琛ュ厖瀹炵幇绾︽潫銆傛湰杞病鏈変慨鏀瑰簲鐢ㄤ唬鐮侊紝涔熸病鏈夊鐞嗚鍒掍功鍘嬬缉銆?
## 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
## 浠诲姟 5锛氱珷鑺傝仛鍚堛€佸鏍′笌鐗堟湰鎺ュ彈/鍥炴粴

### 宸ヤ綔鎬ц川涓庤寖鍥?鏈疆钀藉湴绔犺妭鑱氬悎銆佺珷鑺傚鏍＄粨鏋滄寔涔呭寲銆乻taged/accepted 绔犺妭鐗堟湰銆佹帴鍙?鍥炴粴銆佺増鏈巻鍙插拰 workflow/API 璇诲彇锛涙湭淇敼 Task 6 Canon/Story Bible 娴佺▼銆?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| 绔犺妭鐗堟湰蹇呴』鍥哄畾 accepted SceneRevision 鏄犲皠 | 闃叉鍚庣画鍦烘櫙鍩虹嚎鍙樺寲瑕嗙洊鍘嗗彶绔犺妭 | `ChapterRevisionScene` 鍐欏叆鐪熷疄 `scene_id` 涓?`scene_revision_id` |
| 瀹℃牎缁撴灉鍐欏叆绔犺妭鐗堟湰蹇収 | workflow/API 蹇呴』鑳介噸鏀惧巻鍙插鏍＄粨鏋滐紝涓斾笉淇敼姝ｆ枃 | `ChapterRevision.review_issues/review_summary/review_run_id` |
| 鍥炴粴鍒涘缓鏂?staged 鐗堟湰 | 淇濈暀鏃?accepted 鍘嗗彶骞舵敮鎸佸啀娆?CAS 鎺ュ彈 | `rollback_chapter_revision()` 澶嶅埗鐩爣鍥哄畾鍦烘櫙鍒楄〃 |

### 鍏抽敭瑙勫垯涓庡彇鑸?鑱氬悎鍙帴鍙?accepted plan 涓嬬殑鍦烘櫙锛涚己澶?accepted 鍦烘櫙銆佹棫鍦烘櫙鍩虹嚎鎴栬鍒掓槧灏勪笉涓€鑷存椂鎷掔粷銆傛彁浜ゆ鏌ョ珷鑺?CAS銆乧hapter sync 鐘舵€佸拰姣忎釜鍥哄畾鍦烘櫙鎸囬拡锛涢噸澶嶆彁浜よ繑鍥炲綋鍓?accepted 鐗堟湰锛宍chapter_revision.accepted` outbox 鎸夌増鏈拰鍛戒护骞傜瓑銆?
### 宸插畬鎴愪骇鍑?- 鏂板绔犺妭瀹℃牎 JSONB 瀛楁鍙?Alembic 杩佺Щ銆?- 鎺ュ叆 ChapterGraph/Worker 鐨勭珷鑺傚鏍¤緭鍑哄拰 staged 鐗堟湰鎸佷箙鍖栥€?- 鏂板绔犺妭鐗堟湰璇︽儏 API锛屽巻鍙蹭笌 workflow 杩斿洖鍦烘櫙鏄犲皠銆佸鏍℃憳瑕佸拰 accepted 鎸囬拡銆?- 鏂板 `backend/tests/domain/test_chapter_workflow_task5.py`銆?- 淇绔犺妭涓绘祦绋嬮『搴忥細鎵€鏈夎鍒掑満鏅?accepted 鍚庢墠鍒涘缓绔犺妭 review run锛涜杩愯鍏堣仛鍚?  staged `ChapterRevision`锛屽啀璋冪敤 `ChapterReviewAgent`锛屼笉鍐嶄粠 Planner 鐩磋繛涓嬫父銆?- 鏂板 Worker 闃熷垪鍜?ChapterGraph 椤哄簭鍥炲綊锛岃鐩?review run 骞傜瓑鍒涘缓浠ュ強鈥滆仛鍚堝厛浜庡鏍♀€濄€?
### 楠岃瘉缁撴灉
Task 5 focused `5 passed`锛涚珷鑺傚浘/Agent `18 passed`锛汿ask 4/缂栨帓/API 鍥炲綊 `36 passed`锛涢『搴忎慨澶嶅洖褰?锛堢珷鑺傚浘 + Task 4 Worker锛塦20 passed`锛屽叾涓寘鍚?Worker 鎸佷箙鍖?staged review 杈撳嚭鐨勯泦鎴愭柇瑷€锛汻uff 鍜?compileall 鍧囬€氳繃銆侾laywright 浠嶅彈鐜 `spawn EPERM` 闃绘柇銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| 瀹℃牎 issue 鐩墠瀛樹负绔犺妭鐗堟湰 JSONB 蹇収 | 灏氭湭鎻愪緵鐙珛 issue 琛ㄧ殑缁嗙矑搴︽煡璇?绱㈠紩 | 宸查€氳繃缁撴瀯鍖栧揩鐓ц鍙栨祴璇?|
| Worker 鎺ㄨ繘浠嶆寜涓嬩竴娆?tick 寮傛瀹屾垚 | 鎺ュ彈鎺ュ彛涓嶄細鍚屾绛夊緟绔犺妭鑱氬悎鍜屽鏍?| 鏃㈡湁 Worker 寮傛娴嬭瘯閫氳繃 |
| 娴忚鍣?E2E 鏃犳硶鍚姩瀛愯繘绋?| 缂哄皯鐪熷疄 UI 闂幆璇佹嵁 | 宸插鐜?`spawn EPERM` |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?鐢变富 agent 瀹屾垚鍏ㄩ噺鍚庣妫€鏌ャ€佸闃?Task 5 diff 骞剁粺涓€鎻愪氦锛涢殢鍚庤繘鍏?Task 6 Canon/Story Bible 闂幆銆?| 浜岄樁璁″垝涔︿繚鐣欏畬鏁存€荤洰鏍囷紝涓嶆妸宸ョ▼鐩爣鍘嬬缉鎴愬崟涓€鍓嶇娴佺▼ | 鐢ㄦ埛璁や负鍙繚鐣欑珷鑺備富绾夸細鍋忕鈥滆ˉ鍏ㄥ墠鍚庣鍔熻兘銆佸畬鎴愬畬鏁村垱浣滈棴鐜€濈殑鍒濊》 | 浜岄樁璁捐鏂囨。鍚屾椂淇濈暀鐢ㄦ埛鐩爣銆佸伐绋嬬洰鏍囥€佺姸鎬併€佹帴鍙ｃ€佸墠绔€侀樁娈典氦浠樺拰楠屾敹 |
| 绔犺妭瑙勫垝鐢卞悓涓€涓?`ChapterPlannerAgent` 璐熻矗澶氳疆璁ㄨ | 瑙勫垝婢勬竻銆佽鍒掑弽棣堝拰鍦烘櫙鎷嗚В蹇呴』鍏变韩鍚屼竴绔犺妭涓婁笅鏂囷紝涓嶆柊澧為€氱敤鑱婂ぉ Agent | `ChapterPlannerAgent` 鏄敮涓€绔犺妭瑙勫垝璁ㄨ Agent |
| 涓氬姟 Agent 鏁伴噺涓?7 涓?| `ChapterAggregator` 鏄‘瀹氭€ч鍩熸湇鍔★紝涓嶈皟鐢ㄦā鍨嬶紱Hook銆乄orker銆佽鍒欐鏌ュ拰 checkpoint 涔熶笉璁′负 Agent | `ChapterPlannerAgent`銆乣WritingAgent`銆乣ContinuityAgent`銆乣ReviewAgent`銆乣RevisionAgent`銆乣ChapterReviewAgent`銆乣CanonAgent` |
| 璁″垝鎺ュ彈鍓嶅厛灞曠ず鍊欓€?`SceneBrief[]`锛屾帴鍙楀悗鎵嶇墿鍖栫湡瀹炲満鏅?| 鍦烘櫙涓嶆槸浣滆€呮墜鍔ㄦ嫾鎺ョ珷鑺傜粨鏋勭殑鍓嶇疆鏉′欢锛沘ccepted plan 鎵嶆槸涓诲満鏅槦鍒楁潵婧?| `ChapterWorkflowRead.plan.scene_briefs` 涓庡疄闄?`scenes` 鍒嗙 |
| LangSmith 鍙仛鍙€夊閮ㄨ娴?| 涓氬姟鐘舵€併€佸璁°€佹仮澶嶅拰鐗堟湰涓嶈兘渚濊禆澶栭儴鐩戞帶鏈嶅姟锛涘悓鏃朵繚鐣?Agent Trace銆佽瘎娴嬪拰鎴愭湰鍒嗘瀽鑳藉姏 | PostgreSQL/RunEvent/checkpoint/鏈湴鏃ュ織涓哄簳搴э紝LangSmith fail-open 涓旂敓浜у彧浼犺劚鏁忓厓鏁版嵁 |
| 鍊欓€夎鍒掋€乤ccepted plan 鍜屽緟纭寤鸿蹇呴』鍒嗗紑 | 鏈粡浣滆€呯‘璁ょ殑 AI 鎺ㄦ柇涓嶈兘闈欓粯杩涘叆姝ｅ紡濂戠害鎴栧満鏅槦鍒?| 璇诲彇濂戠害鍖哄垎 `candidate_revision_id`銆乣accepted_revision_id` 鍜屽甫鐘舵€佺殑 `pending_proposals` |
| Planner 鐨勮鍒掑弽棣堝拰閲嶆柊瑙勫垝娌跨敤鍚屼竴绔犺妭/璁″垝琛€缂?| 鍙嶉闇€瑕佹仮澶嶅畬鏁磋璁轰笂涓嬫枃锛屼絾涓嶈兘鎶婃棤鍏崇珷鑺傛垨鏃ц鍒掔殑鍐呭娣峰叆褰撳墠璁″垝 | 璁″垝涔︽敼涓哄厑璁稿悓涓€璁″垝琛€缂樹笅鍒涘缓 Planner 瀛愯繍琛岋紝骞惰姹傛牎楠岀珷鑺傘€佽鍒掑熀绾垮拰 checkpoint |
| 璁″垝瀛楁蹇呴』璁板綍鏉ユ簮鍜岀‘璁ょ姸鎬?| 浣滆€呭～鍐欍€丄I 鎺ㄦ柇銆佷綔鑰呴噰绾?淇敼/鎷掔粷鐨勮涔変笉鍚岋紝涓嶈兘鍙繚瀛樹竴涓棤鏉ユ簮鐨勬渶缁堝€?| 澧炲姞 `PlanFieldProvenance`锛屽尯鍒?`author_confirmed`銆乣ai_suggested`銆乣unresolved` 鍜?`explicitly_omitted` |
| 浣滆€呭喅绛栦娇鐢ㄤ笓闂ㄧ殑璁″垝鍐崇瓥濂戠害 | 閫氱敤鏂囨湰鍙嶉涓嶈冻浠ヨ〃杈鹃€愬瓧娈甸噰绾炽€佷慨鏀广€佹嫆缁濆拰鐗堟湰 CAS | 璁″垝涔﹁ˉ鍏?`PlanDecisionRequest`锛岃姹傛惡甯?`proposal_id`銆乣field_path`銆佸姩浣溿€佹湡鏈涚姸鎬佸拰鐗堟湰鍩虹嚎 |
| Canon 鍊欓€夊彧鏈?`confirm` 鎵嶈兘鍐欏叆姝ｅ紡 Story Bible | `reject` 鍜?`defer` 鏄€欓€夌姸鎬佸彉鍖栵紝涓嶈兘璇啓鎴愭寮忎簨瀹?| 璁″垝涔﹀拰娴佺▼鍥炬槑纭?Canon 鍐崇瓥璇箟鍙婃潵婧愮増鏈?|
| accepted plan 閫氳繃鍙潬浜嬩欢瑙﹀彂鍦烘櫙鐗╁寲鍜岄槦鍒楁仮澶?| 璁″垝鎺ュ彈涓庡満鏅敓鎴愯法浜嬪姟锛屼笉鑳戒緷璧栧崟娆¤繘绋嬪唴璋冪敤锛屽惁鍒欓噸鍚悗鍙兘涓㈠け闃熷垪 | 澧炲姞 `chapter_plan.accepted` outbox銆佸箓绛夋秷璐瑰拰鎸?accepted plan 閲嶅缓闃熷垪绾︽潫 |

## 鍏抽敭瑙勫垯涓庡彇鑸?
- 绔犺妭涓绘祦绋嬪浐瀹氫负锛氫綔鑰呮剰鍥?鈫?Planner 澶氳疆璁ㄨ 鈫?灞曠ず璁″垝鍜?`SceneBrief[]` 鈫?浣滆€呮帴鍙楄鍒?鈫?鑷姩鍒涘缓骞舵寜搴忕敓鎴愬満鏅?鈫?閫愬満鍙嶉/鎺ュ彈 鈫?绔犺妭鑱氬悎銆佸鏍″拰鎺ュ彈 鈫?Canon/Story Bible銆?- `feedback` 鏄户缁鐞嗘寚浠わ紝涓嶆槸杩愯缁堟€侊紱浣滆€呮帴鍙椼€佸彇娑堟垨涓嶅彲鎭㈠澶辫触鎵嶇粨鏉熷綋鍓嶅喅绛栭樁娈点€?- `ChapterWorkflowRead` 鍙娇鐢ㄦ湇鍔＄鏉冨▉ accepted 鎸囬拡锛涘綋鍓嶅€欓€夎鍒掋€佽璁鸿褰曞拰寰呯‘璁ゅ缓璁粦瀹氭椿鍔?Planner 杩愯涓?checkpoint锛屼笉鑳芥寜鏈€鏂拌褰曟帹鏂€?- 鍘嬬缉鏃朵笉鑳藉垹闄?Planner Prompt/Hook銆佸瓧娈垫姇褰便€佹棫鎺ュ彛杩佺Щ銆佽嚜鍔ㄥ満鏅敓鎴愬拰绔埌绔獙鏀剁瓑宸ョ▼绾︽潫锛屽彧鑳藉噺灏戦噸澶嶅彊杩般€?
## 宸插畬鎴愪骇鍑?
- 鏍″噯骞惰ˉ鍏呬簩闃惰璁℃枃妗ｇ殑 Agent 娴佺▼鍥撅紝鎷嗗紑 `WritingAgent`銆佺‘瀹氭€ф鏌ャ€乣ContinuityAgent` 鍜?`ReviewAgent`锛屽苟鏍囨槑 7 涓笟鍔?Agent銆?- 琛ュ厖 LangSmith 鍙€夎娴嬩笌闅愮杈圭晫锛屾槑纭叾涓嶅睘浜?Agent 鎴栦笟鍔＄姸鎬佹満鑺傜偣銆?- 淇 `ChapterWorkflowRead` 鐩爣濂戠害锛氬鍔犲€欓€?`scene_briefs`銆侀€愭潯寤鸿鐘舵€併€佸€欓€?accepted 璁″垝鐗堟湰鍖哄垎鍜?Planner 韬唤鏍囪銆?- 淇濈暀褰撳墠浠ｇ爜灏氭湭瀹炵幇 `ChapterWorkflowRead`銆乣plan_discussion` 鍜?`PlannerDiscussionHook` 鐨勪簨瀹烇紝涓嶆妸璁捐鏂囨。淇敼璁颁负鍔熻兘瀹屾垚銆?- 瀹屾垚绗叚閮ㄥ垎鍚庣璁捐鑷锛岀‘璁ら渶瑕佸湪鍘嬬缉鐗堜腑鏄惧紡淇濈暀绔犺妭闃熷垪璺敱銆丳lanner Hook 鐨勮矾鐢卞墠鎵ц浣嶇疆銆佽法杩愯璁ㄨ琛€缂樺拰鍊欓€?accepted 瑙勫垯銆?- 鏍″噯绗?4銆? 閮ㄥ垎涓庣 6 閮ㄥ垎锛氬弽棣堟寜 plan/scene/chapter 鍒嗗眰锛屽伐浣滄祦鐘舵€佸尯鍒嗗€欓€夎鍒掍笌鐪熷疄鍦烘櫙锛宍ChapterWorkflowRead` 璁ㄨ娑堟伅琛ュ厖杩愯琛€缂樺拰 checkpoint 瀛楁銆?- 璇勫骞舵敼鍐欑 7 閮ㄥ垎鍓嶇璁捐锛氳ˉ鍏呬笂涓嬫枃鏍忔姌鍙犮€佸搷搴斿紡甯冨眬銆侀樁娈典富鍔ㄤ綔鏄犲皠銆丄I 寤鸿閫忔槑搴︺€侀敊璇仮澶嶃€佺増鏈搷浣滆竟鐣屼互鍙婂彸閿?閿洏鍒犻櫎鐨勫彲璁块棶鏇夸唬璺緞銆?- 浣跨敤鏈満宸叉湁鐨勮璁″鏌ユ彁绀鸿瘝鍜屽墠绔璁″鏌ヨ兘鍔涙鏌ヨ鍒掍功鍓?7 閮ㄥ垎锛屽苟涓庡綋鍓嶅悗绔€佸墠绔拰 Worker 婧愮爜閫愰」鏍″噯锛涘閮?`review-spec` 鎶€鑳藉畨瑁呮湭鎴愬姛锛屾湭灏嗗叾缁撴灉鍐掑厖涓哄凡瀹夎瀹℃煡鍣ㄣ€?- 淇 Canon 娴佺▼锛氬彧鏈変綔鑰?`confirm` 鎵嶈兘鐗╁寲姝ｅ紡 Story Bible锛宍reject`/`defer` 鍙繚鐣欏€欓€夊喅绛栫姸鎬併€?- 淇璁″垝鍙嶉鍜岄噸瑙勫垝瑙勫垯锛氬悓涓€绔犺妭銆佸悓涓€璁″垝琛€缂樹笅鎭㈠瀹屾暣璁ㄨ锛屽苟閫氳繃 Planner 瀛愯繍琛屾壙杞介噸瑙勫垝锛涗笉鍐嶇敤鈥滅姝㈣法杩愯璁ㄨ鈥濊繖绉嶄細闃绘柇鍚堟硶鎭㈠鐨勮〃杩般€?- 琛ュ厖 `pending_decision` 鍙?`PlanDecisionRequest` 绾︽潫锛屾槑纭洖绛?Planner銆佹帴鍙楄鍒掋€佸洖绛?鎺ュ彈鍦烘櫙鍜岀珷鑺傚喅绛栫殑寰呭喅鍔ㄤ綔锛涜ˉ鍏呭瓧娈电骇鏉ユ簮鐘舵€侊紝瑕佹眰鏈‘璁ゅ缓璁笉鑳借繘鍏?accepted plan銆?- 琛ュ厖 `chapter_plan.accepted` outbox銆佸箓绛夋秷璐广€侀槦鍒楅噸寤恒€丆AS銆佽鍒掑熀绾垮拰 fencing 绾︽潫锛屾槑纭鍒掓帴鍙楀悗鎵嶈繘鍏ュ満鏅墿鍖栧拰鎸夊簭鐢熸垚銆?
## 楠岃瘉缁撴灉

- 浜岄樁璁捐鏂囨。宸插畬鎴?UTF-8 鍥炶锛涙渶杩戜竴娆?`git diff --check` 閫氳繃銆?- 绔犺妭 Agent銆佺珷鑺傚浘銆佽繍琛岃緭鍏ユ寔涔呭寲鍜岃鍒掑喅绛栫姸鎬佺浉鍏虫祴璇曟鍓嶉獙璇佷负 `15 passed`锛涙湰杞病鏈変慨鏀瑰簲鐢ㄤ唬鐮侊紝鏈洜鏂囨。鏍″閲嶆柊杩愯搴旂敤娴嬭瘯銆?- 婧愮爜鏍稿纭锛氬綋鍓?`ChapterGraph` 浠嶆槸 Planner 鈫?ChapterReview 鈫?Aggregator锛學orker 榛樿绔犺妭杩愯涔熺洿鎺ユ瀯寤鸿鍥撅紱褰撳墠 Hook 鎵ц鎺ュ彛娌℃湁鍙樆鏂矾鐢辩殑 Planner 涓撳睘缁撴灉鏍￠獙闃舵銆?- 鏂囨。绾у洖璇荤‘璁わ細绗?4銆?銆? 閮ㄥ垎鐜板湪缁熶竴浣跨敤鈥滃€欓€夎鍒?鈫?浣滆€呮帴鍙?鈫?鍦烘櫙闃熷垪 鈫?鑱氬悎瀹℃牎 鈫?绔犺妭鎺ュ彈鈥濈殑椤哄簭锛屽苟缁熶竴璁ㄨ琛€缂樺拰鍙嶉璇箟銆?- 鍓嶇璁捐鏂囨。绾у洖璇荤‘璁わ細鍙充晶淇℃伅鏍忎笉鍐嶆壙鎷呯浜屽宸ヤ綔娴侊紝Worker 鑷姩鎺ㄨ繘鍦烘櫙锛宍ChapterWorkflowRead` 鏄樁娈靛拰鎸囬拡鐨勫敮涓€鏉ユ簮锛涘師濮?JSON 浠呬繚鐣欏湪璇︽儏/璋冭瘯鍏ュ彛銆?- 鐜版湁浠ｇ爜鏍稿纭锛欳anon 鏈嶅姟鍙湁 `confirm` 浼氬垱寤烘寮?Canon 鐗堟湰锛涢€氱敤 `DecisionRequest` 浠嶅彧鏈夋枃鏈€侀€夋嫨鍜屾搷浣滃瓧娈碉紝娌℃湁 Planner 涓撳睘閫愬瓧娈靛喅绛栫粨鏋勩€?- 鐜版湁浠ｇ爜鏍稿纭锛歚AgentInputEnvelope` 灏氭棤鐙珛 `chapter_intent`/`plan_discussion`锛岀珷鑺傚弽棣堢洰鍓嶅彧淇濆瓨鍙嶉鍝堝笇锛宍ChapterPlanOutput` 浠嶄娇鐢ㄦ棫鐨?`scene_contracts` 绛夊瓧娈碉紱Worker 灏氭湭鎺ラ€?accepted plan 鍒板満鏅槦鍒楃殑瀹屾暣閾捐矾銆?- 灏濊瘯瀹夎澶栭儴 `ferueda/agent-skills@review-spec` 澶辫触锛屽師鍥犳槸褰撳墠鐜鏃犳硶璁块棶 GitHub锛涙湰杞敼鐢ㄦ湰鏈哄凡鏈夊鏌ユ彁绀鸿瘝鍜屽墠绔璁″鏌ヨ兘鍔涘畬鎴愭鏌ャ€?- 鏈疆娌℃湁杩愯搴旂敤娴嬭瘯銆佹病鏈夊０绉板簲鐢ㄥ姛鑳藉凡缁忓疄鐜帮紱鏃ュ織鍜岃鍒掍功鍧囨寜 UTF-8 鎴愬姛鍥炶锛宍git diff --check` 閫氳繃銆?- 褰撳墠浠撳簱浠嶅瓨鍦ㄦ鍓嶇殑寮€鍙戞棩蹇椾慨鏀瑰拰鏈窡韪?`.vs/`锛屾湰杞湭澶勭悊鏃犲叧宸ヤ綔鍖虹姸鎬併€?
## 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| `ChapterWorkflowRead` 浠嶆槸鐩爣濂戠害锛屽悗绔病鏈夊搴斿疄鐜?| 鍓嶇鏆傛椂涓嶈兘浠庝竴涓潈濞佽鍥炬仮澶嶇珷鑺傚伐浣滃尯 | 宸查€氳繃婧愮爜鎼滅储纭锛屽皻鏈疄鐜?|
| Planner 鎰忓浘銆佽璁恒€佸缓璁瓧娈垫姇褰卞拰涓撳睘 Hook 灏氭湭瀹炵幇 | 褰撳墠 Worker/Planner 浠嶄笉鑳藉畬鎴愮湡姝ｇ殑澶氳疆绔犺妭瑙勫垝闂幆 | 宸查€氳繃婧愮爜鏍稿纭锛屽皻鏈疄鐜?|
| 褰撳墠 `ChapterGraph` 浠嶅彲鑳戒粠 Planner 鐩磋揪 ChapterReview/Aggregator锛屽皻鏈舰鎴?accepted plan 鈫?鍦烘櫙闃熷垪 鈫?鑱氬悎瀹℃牎鐨勭湡瀹炶矾鐢?| 搴旂敤瀹炵幇鍙兘缁曡繃璁″垝鎺ュ彈鍜岄€愬満鍐崇瓥锛岃鍒掍功绾︽潫灏氭湭钀藉湴 | 宸查€氳繃 `chapter_graph.py` 涓?`run_worker.py` 鏍稿纭锛岃鍒掍功宸叉槑纭姝㈣鏃х洿杩炶矾寰?|
| 鐜版湁 `AgentCallable` 娌℃湁 Planner 涓撳睘 Hook 鐨勮矾鐢卞墠鎻掑叆鐐?| 浠呬慨鏀规彁绀鸿瘝鎴栧湪璺敱鍚庢牎楠岄兘涓嶈兘鍙潬闃绘柇鏈‘璁よ鍒?| 宸查€氳繃 `nodes.py` 涓?`hooks.py` 鏍稿纭锛岃鍒掍功宸茶姹傚鍔犻€傞厤灞傛彃鍏ョ偣 |
| Planner 璁ㄨ銆佹剰鍥惧拰瀛楁鏉ユ簮浠嶆湭鎸佷箙鍖栧埌鐩爣濂戠害 | 澶氳疆鎭㈠銆侀€愬瓧娈电‘璁ゅ拰閲嶈鍒掓棤娉曞湪搴旂敤涓彲闈犲疄鐜?| 宸查€氳繃 `schemas.py`銆丳lanner 瀹炵幇鍜岃繍琛屾寔涔呭寲浠ｇ爜鏍稿纭锛屽皻鏈疄鐜?|
| `ChapterWorkflowRead`銆乣pending_decision`銆乣PlanDecisionRequest` 鍜?accepted-plan outbox 浠嶆槸璁捐鐩爣 | 鍓嶇鍜?Worker 鏆傛椂鏃犳硶渚濋潬缁熶竴瑙嗗浘涓庡彲闈犱簨浠舵帹杩涗富娴佺▼ | 宸插啓鍏ヨ鍒掍功锛屼絾鍚庣鏈嶅姟銆佽縼绉诲拰鑷姩鍖栨祴璇曞皻鏈畬鎴?|
| 褰撳墠 `frontend/src/app/page.tsx` 浠嶉泦涓淮鎶よ祫婧愭爲銆佸満鏅紪杈戝拰杩愯鐘舵€侊紝瀹屾暣 `ChapterWorkspace`/`ChapterContextRail` 灏氭湭瀹炵幇 | 鍓嶇杩樹笉鑳芥寜鏂扮殑绔犺妭宸ヤ綔娴佽鍙栬鍥剧ǔ瀹氬憟鐜伴樁娈点€侀樆濉炲拰鍐崇瓥 | 宸查€氳繃婧愮爜闃呰纭锛岃璁℃枃妗ｅ凡琛ュ厖鐩爣杈圭晫锛屽姛鑳藉皻鏈疄鐜?|
| 璁″垝涔︾洰鏍囧绾︿笌褰撳墠瀹炵幇浠嶅瓨鍦ㄨ緝澶у樊璺?| 鍙户缁慨鏀规枃妗ｄ笉鑳界缉鐭簲鐢ㄨ窛绂伙紝瀹炴柦鏃跺繀椤绘寜瀛楁銆佺姸鎬併€佷簨浠跺拰绔埌绔獙鏀堕€愰」钀藉湴 | 宸查€氳繃婧愮爜瀵圭収纭锛屽綋鍓嶆病鏈夊簲鐢ㄥ疄鐜板畬鎴愯瘉鎹?|

## 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
1. 鍏堝疄鐜扮洰鏍囧绾︼細鎵╁睍 `AgentInputEnvelope`銆丳lanner 杈撳叆杈撳嚭 schema銆佸瓧娈垫潵婧愮姸鎬佸拰 `ChapterWorkflowRead`锛屼繚鐣欐棫鏁版嵁鍏煎璇诲彇浣嗘敹绱ф柊寤鸿鍒掑叆鍙ｃ€?2. 瀹炵幇鍚屼竴绔犺妭/璁″垝琛€缂樹笅鐨?Planner 澶氳疆璁ㄨ銆佸缓璁€愰」鍐崇瓥銆乣PlannerDiscussionHook` 鍜岃矾鐢卞墠閫傞厤鐐癸紝骞惰ˉ榻愯璁烘寔涔呭寲涓?checkpoint 鎭㈠娴嬭瘯銆?3. 瀹炵幇 accepted plan 鍐崇瓥銆乣chapter_plan.accepted` outbox銆佸箓绛夋秷璐广€乫encing 鍜屾寜搴忓満鏅槦鍒楋紝绂佹鏃х殑 Planner 鈫?ChapterReview 鈫?Aggregator 鐩磋繛涓绘祦绋嬨€?4. 鎺ュ叆绔犺妭宸ヤ綔鍙板墠绔紝浣跨敤 `ChapterWorkflowRead` 椹卞姩璁″垝銆佸満鏅€佺珷鑺傚鏍″拰 Canon 鍐崇瓥锛屽啀鐢ㄧ湡瀹?Playwright 娴佺▼楠屾敹浠庢剰鍥惧埌 Story Bible 鐨勯棴鐜€?5. 瀹屾垚鏂颁富娴佺▼楠屾敹鍚庯紝鍒犻櫎鏃?`POST /api/chapters/{chapter_id}/plan` 鍏煎鎺ュ彛锛屽苟杩愯鍏ㄩ噺鍚庣銆佸墠绔拰绔埌绔祴璇曘€?
## 浠诲姟 1 淇杩藉姞锛?026-08-06锛?
### 宸ヤ綔鎬ц川涓庤寖鍥?
鏈疆鐢卞瓙 agent 瀹屾垚浠诲姟 1 鍚庣淇锛屼富 agent 瀵?accepted plan CAS銆丳lanner 閲嶆斁骞傜瓑銆侀娆?API 鎺ュ彈鍜?workflow blocked 璇箟杩涜鐙珛楠屾敹锛涙湭澶勭悊杩佺Щ銆乄orker銆佸墠绔拰 Playwright銆?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| 鏂板缓绔犺妭鐨?chapter_intent.text 绾︽潫缁х画淇濈暀 | 鐢ㄦ埛瑕佹眰鑷劧璇█鎰忓浘浣滀负 Planner 鍏ュ彛锛屾棫娴嬭瘯搴旀洿鏂?fixture 鑰屼笉鏄斁瀹藉绾?| test_run_lifecycle.py helper 缁熶竴浼犲叆闈炵┖鎰忓浘 |
| 棣栨鎺ュ彈璁″垝鍏佽娌℃湁 current pointer | 棣栨鎺ュ彈灏氫笉瀛樺湪 accepted plan 鎸囬拡锛岀┖鎸囬拡蹇呴』琛ㄨ揪涓?None | _apply_accept_action 涓嶅啀鎶?None 杞负绌哄瓧绗︿覆 |
| accepted plan 閲嶆斁蹇呴』鍋?CAS 鏍￠獙 | 宸?accepted 鐘舵€佷笉鑳芥帺鐩栨棫 pointer/version 鍐茬獊 | accepted 鍒嗘敮鏍￠獙褰撳墠 pointer 鍜?plan version |
| Planner 閲嶈瘯鎸?source run 鍜岃鍒掕缂樺箓绛?| ready 涓?needs_clarification 閮藉彲鑳借 Worker 閲嶆斁锛屼笉鑳介噸澶嶅啓璇箟娑堟伅 | discussion銆乹uestion銆乸roposal 澧炲姞鍥為€€鍘婚噸閿?|

### 鍏抽敭瑙勫垯涓庡彇鑸?
- ChapterPlannerAgent 娌℃湁鐢熸垚绋冲畾鐨?question_id / proposal_id锛屽洜姝ゆ湰杞寔涔呭寲灞傚湪缂哄皯 ID 鏃舵寜 source run銆佽鍒掕缂樺拰鏂囨湰/瀛楁璺緞澶嶇敤璁板綍銆?- accepted pointer 鎸囧悜鏈?accepted 璁″垝鎴?accepted plan 缂哄皯 SceneBrief 鏄犲皠鏃讹紝ChapterWorkflowRead 杩斿洖 phase=blocked銆?
### 宸插畬鎴愪骇鍑?
- 淇 backend/app/domain/chapters.py銆乥ackend/app/services/generation_runs.py銆?- 鏂板/鏇存柊 backend/tests/domain/test_chapter_workflow.py銆乥ackend/tests/api/test_run_lifecycle.py銆?- 鐢熸垚 codex-handoff/2026-08-06-chapter-workbench-task1-fix-report.md銆?
### 楠岃瘉缁撴灉

- 鐩稿叧鍚庣娴嬭瘯锛?9 passed銆?- 鐩爣鏂囦欢 Ruff锛欰ll checks passed!銆?- 淇鎶ュ憡宸叉寜 UTF-8 鍥炶銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| Alembic migration 灏氭湭琛ラ綈 | 鏂拌〃/鏂板垪涓嶈兘鐩存帴鐢ㄤ簬鐢熶骇鏁版嵁搴撳崌绾?| 宸茬‘璁わ紝鐣欑粰鍚庣画杩佺Щ浠诲姟 |
| Worker銆乷utbox 閲嶆斁鍜?Playwright 涓绘祦绋嬫湭瑕嗙洊 | 浠嶄笉鑳藉绉板畬鏁寸珷鑺傞棴鐜凡钀藉湴 | 宸茬‘璁わ紝灏氭湭瀹炵幇 |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
鐢卞悗缁换鍔¤ˉ榻?Alembic migration銆乄orker 闃熷垪鎭㈠鍜屽墠绔珷鑺傚伐浣滃彴锛屽啀杩涜鍏ㄩ噺闆嗘垚楠屾敹銆?
## Task 1 娈嬩綑淇涓庡瀹￠€氳繃
### 宸ヤ綔鎬ц川涓庤寖鍥?鏈疆鏀跺彛浠诲姟 1 鐨勬畫浣欑己鍙ｏ細鍘绘帀閲嶅鐨勮鍒掔墿鍖栬皟鐢紝淇 feedback 瀛愯繍琛岃缂樻柟鍚戯紝琛ラ綈鍥炲綊娴嬭瘯锛屽苟閲嶆柊璺戦獙璇併€?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| accepted plan 鐨勫満鏅墿鍖栦笌 outbox 鍙繚鐣欎竴鏉′簨鍔¤矾寰?| 閬垮厤璁″垝鎺ュ彈鏃堕噸澶嶅彂鍑?chapter_plan.accepted | backend/app/services/generation_runs.py |
| feedback 瀛愯繍琛?supersedes_run_id 鍙寚鍚戠埗杩愯 | 璁╂浛浠ｅ叧绯诲崟鍚戜笖鍙璁?| backend/app/services/generation_runs.py銆乥ackend/tests/api/test_run_lifecycle.py |

### 鍏抽敭瑙勫垯涓庡彇鑸?
- accept_chapter_plan_revision 缁熶竴璐熻矗 accepted 璁″垝鐨勫浐瀹氬満鏅槧灏勫拰 outbox銆?- workflow_read 瀵规偓绌?accepted pointer銆佺己澶?scene mapping 缁存寔 blocked銆?- Planner 閲嶆斁浠嶆寜 source_run_id 鍜岃鍒掕缂樺幓閲嶏紝涓嶆妸閲嶈瘯鍐欐垚鏂拌涔夎褰曘€?
### 宸插畬鎴愪骇鍑?
- 鍒犻櫎 _apply_accept_action 涓噸澶嶇殑 materialize_chapter_plan 璋冪敤锛屽苟娓呯悊鏈娇鐢ㄥ鍏ヤ笌灞€閮ㄥ彉閲忋€?- 淇 feedback 瀛愯繍琛岀殑 supersedes_run_id 璇箟锛屽苟琛ラ綈瀵瑰簲鍥炲綊銆?- 缁х画淇濈暀浠诲姟 1 鐨勫€欓€夋寔涔呭寲銆亀orkflow read 鍜岃鍒掓帴鍙椾簨鍔′慨澶嶃€?
### 楠岃瘉缁撴灉

- pytest锛?0 passed銆?- Ruff锛欰ll checks passed銆?- compileall锛氶€氳繃銆?- alembic heads锛歛1b2c3d4e5f6 (head)銆?- git diff --check锛氶€氳繃銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| Task 2 鐨?Worker銆乷utbox銆丳laywright 涓绘祦绋嬩粛鏈疄鐜?| 绔犺妭闂幆杩樻病鏈夌湡姝ｆ墦閫?| 宸茬煡锛屾湭寮€濮?|

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
缁х画鎵ц浠诲姟 2锛歐orker 闃熷垪銆乤ccepted-plan outbox 娑堣垂涓庨噸鏀俱€佺‘瀹氭€?fake provider 鍜?Playwright worker 澶瑰叿銆?
## 浠诲姟 2锛歐orker銆佽縼绉讳笌纭畾鎬?E2E

### 宸ヤ綔鎬ц川涓庤寖鍥?
鏈疆鐢卞瓙 agent 瀹炵幇浠诲姟 2锛屼富 agent 鐙珛澶嶆牳骞朵慨澶?Worker 绔犺妭瑙勫垝璺敱銆丩angGraph 涓棿缁撴灉浼犻€掋€佽鍒掑瓧娈垫姇褰便€乤ccepted pointer 鏍￠獙銆佸満鏅繍琛?outbox 鍜屾祴璇曢殧绂汇€傝寖鍥磋鐩?Worker銆佺珷鑺傚浘銆佽繍琛岀姸鎬併€佽縼绉诲洖褰掋€丳laywright Worker 鍚姩閰嶇疆鍜屾渶灏忕珷鑺傚伐浣滄祦娴嬭瘯锛涙湭淇敼鍓嶇涓婚〉闈?API service/绫诲瀷鏂囦欢銆?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| accepted plan 鍚庡厛鎭㈠绗竴涓湭瀹屾垚鍦烘櫙锛屼笉鑳芥部鏃?ChapterGraph 鐩磋揪绔犺妭瀹℃牎/鑱氬悎 | 璁″垝鎺ュ彈鏄満鏅槦鍒楃殑鍞竴鍏ュ彛锛屼綔鑰呮湭鎺ュ彈璁″垝鍓嶄笉鑳界敓鎴愭鏂?| `RunWorker._consume_plan_outbox()`銆乣ChapterGraph` 鐨?`new_chapter` 鏆傚仠鍒嗘敮 |
| Planner 璁ㄨ瀛楁鍙姇褰辩粰绔犺妭瑙勫垝杩愯 | 瑙勫垝涓婁笅鏂囦笉鑳芥硠婕忓埌鍦烘櫙銆佺珷鑺傚鏍℃垨 Canon Agent | `RunWorker._build_envelope()` 鎸?`decision_target=plan` 鎶曞奖 |
| Worker 閲嶅惎鍜岄噸澶?outbox 鎶曢€掑繀椤绘寜 accepted pointer 涓庡満鏅槧灏勫箓绛夋仮澶?| 杩涚▼閲嶅惎涓嶈兘鍒涘缓閲嶅 scene run锛屼篃涓嶈兘鎺ュ彈鎮┖/鏃ц鍒掍簨浠?| accepted pointer 鏍￠獙銆乣(chapter_id, scene_id, plan_revision_id)` 鏌ラ噸銆佸満鏅?`run_queued` outbox |
| 纭畾鎬?Fake provider 涓嶈闂閮ㄦā鍨嬶紝Playwright 鍚屾椂鍖呭惈 API銆佸墠绔拰 Worker | 涓绘祦绋嬮獙鏀跺繀椤诲彲閲嶅锛屼笉鑳戒緷璧栫湡瀹炴ā鍨嬫垨浜哄伐绛夊緟 | 鐜版湁 Fake 璇箟銆乣frontend/playwright.config.ts` Worker webServer銆乣chapter-workflow.spec.ts` |

### 鍏抽敭瑙勫垯涓庡彇鑸?
- `new_chapter` Planner 杩斿洖 `ready` 鍚庡彧鍐欏叆 pending 鍊欓€夊苟杩涘叆 `waiting_feedback`锛屼綔鑰呮帴鍙楀墠涓嶈兘鍚姩绔犺妭瀹℃牎銆佽仛鍚堟垨鐪熷疄鍦烘櫙銆?- `planner_output` 鏄繍琛屽唴缁撴瀯鍖栦腑闂寸粨鏋滐紝鍔犲叆 `ChapterRunState` 浠ヤ究 LangGraph 鐘舵€佸悎骞跺悗鐢?Worker 鍦ㄥ悓涓€浜嬪姟璋冪敤 `persist_planner_output`锛涘畠涓嶆槸 accepted plan銆?- accepted plan outbox 娑堣垂蹇呴』鏍￠獙鏈嶅姟绔?`ChapterPlanRevisionLink`锛屼笉鑳戒粎淇′换浜嬩欢 payload锛涘満鏅繍琛屽垱寤哄悓鏃跺啓鍏ユ爣鍑?`run_queued` outbox銆?- 娴嬭瘯鏁版嵁搴撴瘡涓祴璇曞紑濮嬪墠娓呯悊璺ㄧ嫭绔?Worker 浼氳瘽鎻愪氦鐨?plan outbox锛岄伩鍏嶅巻鍙?accepted 浜嬩欢閲嶆柊椹卞姩褰撳墠娴嬭瘯銆?
### 宸插畬鎴愪骇鍑?
- 鏇存柊 `backend/app/runtime/run_worker.py`锛氭仮澶?Planner 鎰忓浘/璁ㄨ/闂/寤鸿锛屾寔涔呭寲鍊欓€夛紝娑堣垂 accepted plan outbox锛屾寜椤哄簭鍒涘缓绗竴涓?scene run锛屾敮鎸侀噸澶嶆姇閫?Worker 閲嶅惎鏌ラ噸锛屽苟琛ラ綈鍦烘櫙杩愯 outbox銆?- 鏇存柊 `backend/app/agents/chapter_graph.py` 涓?`backend/app/agents/state.py`锛氶樆鏂?`new_chapter` 鐨勬棫 Planner 鈫?Review/鑱氬悎鐩磋繛锛屼繚鐣欏€欓€変腑闂寸粨鏋溿€?- 淇濈暀骞堕獙璇佷换鍔?1 鐨?Alembic migration 涓庝簲寮犺鍒掑伐浣滄祦琛紱鏂板杩佺Щ鍥炲綊娴嬭瘯瑕嗙洊銆?- 鏂板 `backend/tests/runtime/test_chapter_workflow_worker_task2.py`锛屾洿鏂扮珷鑺?Worker 鍥炲綊鍜屾祴璇?fixture銆?- 鏇存柊 `frontend/playwright.config.ts`锛屽鍔?Worker webServer锛涙柊澧?`frontend/tests/chapter-workflow.spec.ts` 鏈€灏忔祦绋嬫祴璇曘€?- 鐢熸垚 `codex-handoff/2026-08-06-chapter-workbench-task2-report.md` 涓庡鏍稿寘銆?
### 楠岃瘉缁撴灉

- 杩佺Щ銆乄orker銆佺珷鑺傞摼璺祴璇曪細`19 passed, 1 skipped`銆?- Ruff锛歚All checks passed`銆?- `compileall`锛氶€氳繃銆?- 鍓嶇 `npm run typecheck`锛氶€氳繃銆?- `git diff --check`锛氶€氳繃锛堜粎鏃㈡湁鎹㈣绗︽彁绀猴級銆?- Playwright锛歚npx playwright test tests/chapter-workflow.spec.ts --project=chromium` 鍦ㄥ綋鍓嶇幆澧冭繑鍥?`spawn EPERM`锛屾祻瑙堝櫒/webServer 鏈兘鍚姩锛屾湭灏嗗叾璁颁负閫氳繃銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| 褰撳墠鐜绂佹 Playwright 瀛愯繘绋嬪惎鍔?| 灏氭湭鑾峰緱鐪熷疄 API + 鍓嶇 + Worker 鐨勬祻瑙堝櫒杩愯璇佹嵁 | 宸插鐜?`spawn EPERM`锛屼唬鐮佷笌绫诲瀷妫€鏌ュ凡閫氳繃 |
| Worker 浠?`GenerationRun.status == "accepted"` 鍒ゆ柇鍦烘櫙瀹屾垚 | 鍚庣画寮曞叆鏂扮殑鍦烘櫙瀹屾垚缁堟€佹椂闇€鍚屾闃熷垪鎺ㄨ繘鏉′欢 | 宸插湪浠诲姟鎶ュ憡涓褰曪紝寰呭満鏅槦鍒椾换鍔＄粺涓€鏀跺彛 |
| outbox consumer 浣跨敤 `consumed` 鐘舵€侊紝鑰岀幇鏈?publisher 浣跨敤 pending/publishing/published | 閮ㄧ讲鏃堕渶瑕佹槑纭笟鍔℃秷璐硅€呬笌鍙戝竷鑰呯殑鑱岃矗杈圭晫 | 宸查€氳繃浠ｇ爜鏍稿锛屽皻鏈仛鐪熷疄閮ㄧ讲閲嶆斁楠岃瘉 |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
浠诲姟 2 鐨勪唬鐮併€佽縼绉诲洖褰掑拰闈欐€佹鏌ュ凡瀹屾垚锛涙祻瑙堝櫒 E2E 鍙楃幆澧冮樆濉炪€備笅涓€姝ョ户缁墽琛屼换鍔?3锛氭帴鍏?`ChapterWorkflowRead` 椹卞姩鐨勭珷鑺傚伐浣滃彴鍓嶇锛屽苟鍦ㄥ悗缁畬鏁存梾绋嬩腑閲嶆柊杩愯 Playwright 楠屾敹銆?
## 浠诲姟 3锛欳hapterWorkflowRead 鍓嶇宸ヤ綔鍙?
### 宸ヤ綔鎬ц川涓庤寖鍥?
鏈疆瀹屾垚绔犺妭宸ヤ綔鍙板墠绔渶灏忛棴鐜細绔犺妭鍏ュ彛銆亀orkflow 鏉冨▉璇诲彇銆佹剰鍥捐緭鍏ャ€丳lanner 鍙嶉/璁″垝鎺ュ彈鍏ュ彛銆佸€欓€夎鍒掍笌鍦烘櫙闃熷垪灞曠ず銆備繚鐣欐棫鍗曞満鏅紪杈戙€佽繍琛岄潰鏉裤€佺増鏈巻鍙层€佸洖婊氬拰 Story Bible 鑳藉姏锛涙湭淇敼鍚庣銆?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| 鏂扮珷鑺備富娴佺▼閫氳繃 `/runs` 涓?`/workflow` 鎺ュ叆 | 绔犺妭宸ヤ綔鍙板繀椤讳互 `ChapterWorkflowRead` 涓烘潈濞佺姸鎬侊紝涓嶈兘缁х画渚濊禆鏃ц鍒掑垵濮嬪寲鎸夐挳 | `getChapterWorkflow`銆乣createChapterRun` 涓庨〉闈?workflow 鍒嗘敮 |
| 璁″垝鍊欓€変笌 accepted 璁″垝鍒嗗紑灞曠ず | 鏈粡浣滆€呯‘璁ょ殑 Planner 寤鸿涓嶈兘鐩存帴杩涘叆姝ｅ紡璁″垝鎴栧満鏅鏂?| `candidate_revision_id`銆乣accepted_revision_id` 鍜?`scene_briefs` 鍒嗘爮灞曠ず |
| 鏃у満鏅紪杈戝垎鏀繚鎸佷笉鍙?| 鏂扮珷鑺傚叆鍙ｄ笉鑳界牬鍧忕幇鏈夊満鏅紪杈戙€佽繍琛屽拰鐗堟湰鎿嶄綔 | `selectedScene` 鍒嗘敮鍘熸牱淇濈暀锛岀珷鑺傚伐浣滃彴鍙湪鏃犻€変腑鍦烘櫙鏃舵樉绀?|

### 鍏抽敭瑙勫垯涓庡彇鑸?
椤甸潰闃舵銆佸緟鍐冲姩浣溿€侀樆鏂師鍥犲拰鐗堟湰鎸囬拡鐩存帴鏉ヨ嚜 workflow锛涘墠绔彧缁存姢绔犺妭鎰忓浘鑽夌銆丳lanner 鍙嶉鑽夌鍜岄€変腑绔犺妭銆傝璁哄尯銆佸€欓€夎鍒掑拰鍦烘櫙闃熷垪鍧囦负灞曠ず鍖猴紝鏈湪鍓嶇鑷鎺ㄦ柇 accepted 鎴栭槦鍒楅『搴忋€?
### 宸插畬鎴愪骇鍑?
- 鏂板 `ChapterWorkflowRead` 鍓嶇绫诲瀷鍙婄珷鑺傝繍琛?宸ヤ綔娴?API 瀹㈡埛绔€?- 鍦?`page.tsx` 澧炲姞绔犺妭宸ヤ綔鍙颁氦浜掍笌鐘舵€佽疆璇€?- 绔犺妭鏍戜娇鐢ㄧǔ瀹氱殑 `chapter-item-${id}` 鎸夐挳杩涘叆宸ヤ綔鍙帮紝璁″垝鍙嶉/鎺ュ彈/鍙栨秷鍏ュ彛浣跨敤鐙珛 test id銆?- 澧炲姞宸ヤ綔鍙版牱寮忓拰 UI 娴嬭瘯銆?- 鍐欏叆浠诲姟鎶ュ憡 `codex-handoff/2026-08-06-chapter-workbench-task3-report.md`銆?
### 楠岃瘉缁撴灉

`npm run typecheck` 閫氳繃锛沗npx playwright test ... --list` 鎴愬姛鍒楀嚭 2 涓祴璇曘€傜湡瀹?Playwright 杩愯鍜?`npm run build` 鍧囪褰撳墠鐜 `spawn EPERM` 闃绘柇锛屾湭瀹ｇО閫氳繃銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| 娴忚鍣?Next 鏋勫缓瀛愯繘绋嬫棤娉曞惎鍔?| 灏氭棤鐪熷疄 UI 鏃呯▼璇佹嵁 | 宸插鐜?`spawn EPERM` |
| 宸ヤ綔鍙颁粛宓屽叆 `page.tsx` | 鍚庣画闃舵闇€瑕佺户缁媶鍒嗙粍浠讹紝闄嶄綆椤甸潰澶嶆潅搴?| 宸插畬鎴愭渶灏忓姛鑳斤紝灏氭湭鎷嗗垎 |
| 鏃ц鍒掑垵濮嬪寲澶勭悊鍑芥暟浠嶄繚鐣?| 鍏煎鍏ュ彛灏氭湭娓呯悊锛岄渶绛夊緟瀹屾暣涓绘祦绋嬪洖褰掑悗鍒犻櫎 | 鏂板伐浣滃彴鏈皟鐢ㄦ棫 POST锛岄潤鎬佹牳瀵归€氳繃 |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
鍦ㄥ厑璁歌繘绋嬪垱寤虹殑鐜閲嶆柊杩愯绔犺妭宸ヤ綔鍙?Playwright UI 娴嬭瘯锛涢殢鍚庣户缁换鍔?4 鐨勫満鏅槦鍒椾笌閫愬満鍐崇瓥锛屽疄鐜?accepted plan 鍚庣敱 Worker 椹卞姩鐨勫満鏅富闃熷垪銆?
## 浠诲姟 3 楠屾敹淇锛氬鑸爲浜や簰閿氱偣鏀剁揣

### 宸ヤ綔鎬ц川涓庤寖鍥?
涓?agent 澶嶆牳浠诲姟 3 鍚庡彂鐜?UI 娴嬭瘯鍘熷厛鐐瑰嚮椤圭洰鍚?鍗峰悕鏂囨湰锛岃€岄〉闈㈢湡姝ｈ礋璐ｅ睍寮€璧勬簮鏍戠殑鏄浉閭绘寜閽€傛湰杞彧淇瀵艰埅鏍戠殑绋冲畾浜や簰閿氱偣鍜屽搴旀祴璇曪紝涓嶆敼鍙樺悗绔绾︽垨鏃у満鏅紪杈戝垎鏀€?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| 椤圭洰銆佸嵎銆佺珷鍏ュ彛浣跨敤绋冲畾 test id | 鏂囨湰鑺傜偣涓嶆槸浜や簰鎺т欢锛屾祴璇曞簲缁戝畾鐪熷疄鐢ㄦ埛鎿嶄綔鍏ュ彛 | `project-toggle-{id}`銆乣volume-toggle-{id}`銆乣chapter-item-{id}` |

### 鍏抽敭瑙勫垯涓庡彇鑸?
绔犺妭宸ヤ綔鍙颁粛浠?`ChapterWorkflowRead` 涓烘潈濞佺姸鎬佹簮锛涙湰杞粎鏀剁揣瀵艰埅鍏ュ彛銆備弗鏍?TDD 鐨勭孩缁胯繃绋嬩粛娌℃湁鐪熷疄娴忚鍣ㄨ瘉鎹紝鍥犱负褰撳墠鐜鐨?Playwright 瀛愯繘绋嬭 `spawn EPERM` 闃绘柇銆?
### 宸插畬鎴愪骇鍑?
- 涓洪」鐩拰鍗峰睍寮€鎸夐挳澧炲姞绋冲畾 `data-testid`銆?- 鏇存柊绔犺妭宸ヤ綔鍙?Playwright 娴嬭瘯锛屼娇鐢ㄧ湡瀹炲睍寮€鎸夐挳杩涘叆绔犺妭銆?
### 楠岃瘉缁撴灉

- `npm run typecheck`锛氶€氳繃銆?- `npx playwright test tests/chapter-workflow.spec.ts --project=chromium --list`锛氬垪鍑?2 涓祴璇曘€?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| 鐪熷疄 Playwright 娴忚鍣ㄦ墽琛岃鐜绂佹鍒涘缓瀛愯繘绋?| 灏氭棤娴忚鍣ㄨ繍琛屾椂璇佹嵁 | 宸插鐜?`spawn EPERM`锛屼唬鐮佽В鏋愬拰绫诲瀷妫€鏌ラ€氳繃 |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
杩涘叆浠诲姟 4锛氬疄鐜?accepted plan 涔嬪悗鐨勫満鏅槦鍒楁帹杩涖€侀€愬満鏅繍琛屽叆鍙ｅ拰鍦烘櫙鍙嶉/鎺ュ彈鐘舵€佽鍙栵紱鍓嶇涓嶈嚜琛屾帹鏂槦鍒楅『搴忋€?
## 浠诲姟 4锛氬満鏅槦鍒椾笌閫愬満瀹￠槄

### 宸ヤ綔鎬ц川涓庤寖鍥?
鏈疆钀藉湴 accepted plan 涔嬪悗鐨勫満鏅槦鍒楁仮澶嶃€佹寜璁″垝椤哄簭鎺ㄨ繘銆佸満鏅繍琛屽熀绾跨粦瀹氥€侀€愬満鍐崇瓥闃绘柇銆佸弽棣堝奖鍝嶉棴鍖呭拰 workflow 鏉冨▉璇诲彇銆備富 agent 鍙﹁ˉ榻愪簡鈥滅洿鎺ュ垱寤哄悗缁満鏅繍琛屼笉寰楃粫杩囬槦鍒椻€濈殑鍥炲綊娴嬭瘯涓庡垱寤烘湡鏍￠獙锛屽苟淇 Worker 娴嬭瘯璺ㄤ細璇濇薄鏌撱€?
### 鐢ㄦ埛涓诲鍐崇瓥

| 鍐崇瓥 | 鍘熷洜涓庡彇鑸?| 钀界偣 |
| --- | --- | --- |
| accepted plan 鍚庡彧鎭㈠褰撳墠鍙繍琛屽満鏅?| 鍦烘櫙椤哄簭鐢辫鍒掓槧灏勫喅瀹氾紝浣滆€呮湭鎺ュ彈鍓嶄笉鑳借烦杩囨垨骞惰鐢熸垚 | `RunWorker._ensure_next_scene_run`銆乣ChapterPlanSceneLink.sort_order` |
| 鍦烘櫙杩愯蹇呴』缁戝畾璁″垝涓庡満鏅熀绾?| 鏃ц鍒掓垨鏃?accepted revision 涓嶈兘缁х画鍐欏叆姝ｆ枃 | `plan_revision_id`銆乣base_scene_revision_id`銆佸満鏅綊灞炲拰 CAS 鏍￠獙 |
| 鍒涘缓闃舵涔熻闃绘柇鍚庣画鍦烘櫙 | 鍙湪鍐崇瓥闃舵鏍￠獙浠嶅彲閫氳繃鎵嬪姩 API 鍒涘缓鍚庣画 run | `start_generation_run` 璋冪敤鍦烘櫙闃熷垪鍓嶇疆鏍￠獙锛涘洖褰掓祴璇曞厛绾㈠悗缁?|
| Worker 娴嬭瘯娓呯悊鐙珛浼氳瘽鍓綔鐢?| Worker 浼氭彁浜よ繍琛屻€佹槧灏勫拰 outbox锛屽崟绾洖婊氭祴璇曚細璇濅笉瓒充互闅旂闃熷垪鎭㈠ | `backend/tests/conftest.py` 娓呯悊杩愯銆佷簨浠躲€佽鍒掓槧灏勫拰 outbox |

### 鍏抽敭瑙勫垯涓庡彇鑸?
- Worker 鎸?accepted plan 鐨勫浐瀹?`chapter_plan_scene_links.sort_order` 鎭㈠锛岄噸澶?tick/outbox replay 浠?`(chapter_id, scene_id, plan_revision_id)` 鏌ラ噸銆?- 鍓嶄竴鍦烘櫙蹇呴』鍚屾椂鏈夊悓涓€璁″垝涓嬬殑 accepted run 鍜?`Scene.accepted_scene_revision_id`锛屽悗缁満鏅墠鍏佽鍏ラ槦鎴栧垱寤恒€?- Worker 鍦?`_process_one()` 棰嗗彇 queued 鍦烘櫙鍓嶉噸鏂伴攣瀹氬苟鏍￠獙绔犺妭 accepted plan pointer锛涜鍒掓浛鎹㈠悗鐨勬棫 run 鍘熷瓙鏍囪涓?`superseded`锛屼笉浼氶鍙栫绾︽垨鏋勫缓 graph銆?- 鍦烘櫙鍙嶉璁板綍褰撳墠鍦烘櫙鍙婁笅娓稿奖鍝嶉棴鍖咃紝鍙楀奖鍝嶆棫杩愯鏍囪 `superseded`锛泈orkflow 杩斿洖 `affected_scene_ids`銆乣stale_scene_ids` 鍜屽満鏅骇闃绘柇鍘熷洜銆?- 鏃у満鏅紪杈戙€佺増鏈拰鍥炴粴鍏ュ彛淇濇寔涓嶅彉锛涙柊闃熷垪鍙秷璐?accepted plan 鏄犲皠銆?
### 宸插畬鎴愪骇鍑?
- 鏇存柊 `backend/app/runtime/run_worker.py`锛歛ccepted-plan outbox 娑堣垂銆侀『搴忛槦鍒楁仮澶嶃€佸満鏅繍琛屽熀绾垮拰杩愯 outbox銆?- 鏇存柊 `backend/app/services/generation_runs.py`锛氳鍒?鍦烘櫙褰掑睘銆佸垱寤烘湡鍜屽喅绛栨湡椤哄簭闃绘柇銆佸弽棣堝奖鍝嶉棴鍖呫€?- 鏇存柊 `backend/app/domain/chapter_orchestration.py` 涓?`backend/app/domain/chapters.py`锛氳鍒掗『搴忚鍙栧拰 workflow 鍦烘櫙鐘舵€併€?- 鏂板 `backend/tests/runtime/test_chapter_workflow_task4.py`锛岃ˉ鍏呮墜鍔ㄨ烦杩囧満鏅殑澶辫触鍥炲綊涓庝慨澶嶃€?- 鏂板 `test_worker_rejects_queued_scene_run_after_plan_replacement`锛岄獙璇佹棫璁″垝 queued run 鍦?graph 鏋勫缓鍓嶈 `superseded`銆?- 鏇存柊 `backend/tests/conftest.py` 浠ラ殧绂?Worker 璺ㄤ細璇濇彁浜ょ殑鏁版嵁銆?- 鍐欏叆 `codex-handoff/2026-08-06-chapter-workbench-task4-report.md` 涓庡鏍稿寘銆?
### 楠岃瘉缁撴灉

- TDD 绾㈢伅锛歚test_manual_run_cannot_skip_unaccepted_previous_scene` 鍦ㄥ垱寤烘湡鏍￠獙鎺ュ叆鍓嶅け璐ワ紝纭鏈娴嬭瘯鐜扮姸鎺╃洊銆?- 缁跨伅锛氫换鍔?4/浠诲姟 2/缂栨帓鍩?杩愯 API focused suite `35 passed`銆?- 淇鍚?Task 4 鑱氱劍娴嬭瘯锛歚10 passed`锛汿ask 4/Task 2/API 缁勫悎娴嬭瘯锛歚28 passed`銆?- 鏈嶅姟/API 娴嬭瘯鍏ㄩ噺锛氶€氳繃銆?- Ruff锛氱浉鍏冲悗绔枃浠?`All checks passed!`銆?- `compileall`锛氶€氳繃銆?- 鍓嶇 `npm run typecheck`锛氶€氳繃銆?- 娴忚鍣?Playwright 浠嶅彈褰撳墠鐜 `spawn EPERM` 闃绘柇锛屾湭鎶婃祻瑙堝櫒鏃呯▼璁颁负閫氳繃銆?
### 褰撳墠涓嶈冻涓庨闄?
| 闂 | 褰卞搷 | 楠岃瘉鐘舵€?|
| --- | --- | --- |
| 鍦烘櫙 feedback 浠嶅鐢ㄥ綋鍓?run 鐨?waiting_feedback 鐘舵€侊紝娌℃湁鍗曠嫭鍒涘缓 RevisionAgent 瀛?run | 鍙嶉鍚庣殑琛ヤ竵鐢熸垚浠嶄緷璧栫幇鏈夊満鏅繍琛屾仮澶嶉摼锛岃嫢浜у搧瑕佹眰鍙嶉绔嬪嵆鐢熸垚鐙珛瀛愯繍琛岃繕闇€缁х画鎺ョ嚎 | 瀛?agent 鎶ュ憡宸茬‘璁わ紝浠诲姟 4 focused 娴嬭瘯鏈鐩栫嫭绔嬪瓙杩愯 |
| 鍦烘櫙闃熷垪鐢?Worker 涓嬩竴娆?tick 寮傛鎺ㄨ繘 | accept API 杩斿洖涓庝笅涓€鍦烘櫙 run 鍒涘缓涔嬮棿瀛樺湪鐭殏绛夊緟 | Worker recovery 娴嬭瘯閫氳繃锛岀湡瀹炴祻瑙堝櫒浠嶆湭杩愯 |
| `base_scene_revision_id` 浠嶅瓨浜庝笉鍙彉 `normalized_input`锛屾湭鏂板鐙珛鏁版嵁搴撳垪 | 鏌ヨ鍜岃縼绉绘棫鏁版嵁闇€缁х画渚濊禆杈撳叆淇″皝鍏煎 | focused run 鍒涘缓/鎭㈠娴嬭瘯閫氳繃 |
| accepted plan 鐨?`scene_briefs` JSONB 娌℃湁鏁版嵁搴撶骇涓嶅彲鍙樹繚鎶?| 鑻ユ湭鏉ュ鍔?accepted plan 鍘熷湴缂栬緫鍏ュ彛锛學orker 浠嶉渶鏀逛负璇诲彇涓嶅彲鍙樺揩鐓ф垨鎷掔粷淇敼 | 褰撳墠棰嗗煙/API 璺緞涓嶆彁渚?accepted plan 鍘熷湴缂栬緫锛涘凡璁板綍涓哄悗缁不鐞嗛」 |

### 褰撳墠鏈畬鎴愪簨椤逛笌涓嬩竴姝?
杩涘叆浠诲姟 5锛氱珷鑺傝仛鍚堛€佸鏍°€乻taged/accepted 绔犺妭鐗堟湰銆佸奖鍝嶉棴鍖呬笌绔犺妭鎺ュ彈/鍥炴粴锛涘畬鎴愬悗鍐嶆帴浠诲姟 6 鐨?Canon/Story Bible 闂幆銆傚畬鏁?Playwright 鏃呯▼闇€鍦ㄥ厑璁稿垱寤哄瓙杩涚▼鐨勭幆澧冮噸鏂拌繍琛屻€?

## Task 6锛欳anon/Story Bible 涓庣珷鑺傚伐浣滃彴鏀跺彛

### 宸ヤ綔鑼冨洿
缁х画鎵ц绔犺妭宸ヤ綔鍙拌鍒掞紝瑕嗙洊绔犺妭鎺ュ彈 outbox 鐨?Canon 娑堣垂閲嶈瘯銆佺珷鑺?workflow 鐨?Canon 鎽樿銆佺珷绾?Story Bible 鍏ュ彛鍜岀珷鑺傚鏍稿姩浣滃尯銆?
### 宸插畬鎴?- Worker 鍙秷璐瑰彲璋冨害鐨勭珷鑺傛帴鍙?outbox锛涗笟鍔?handler 澶辫触鏃惰褰?`failed`銆侀€掑灏濊瘯娆℃暟骞舵寜 5 绉掗€€閬匡紝鎴愬姛娑堣垂浼氭竻绌?`next_attempt_at`銆?- `ChapterWorkflowRead` 鍓嶇濂戠害琛ラ綈绔犺妭瀹℃牎闂銆佸鏍℃憳瑕佸拰鐗堟湰鍘嗗彶瀛楁銆?- 绔犺妭宸ヤ綔鍙板鍔?Canon 鏉ユ簮鐗堟湰銆佸緟鍐冲€欓€夊拰褰撳墠绔犺妭鐗堟湰鎽樿锛涙棤鍦烘櫙閫変腑鏃舵樉绀虹珷绾?Story Bible銆?- 绔犺妭宸ヤ綔鍙板鍔犲惎鍔ㄧ珷鑺傚鏍搞€佹帴鍙?staged 绔犺妭鐗堟湰鍜屾彁浜ょ珷鑺傚鏍″弽棣堝叆鍙ｃ€?- 鏂板鍓嶇鍥炲綊鏂█锛岀珷鑺傚叆鍙ｆ墦寮€宸ヤ綔鍙板悗蹇呴』鏄剧ず绔犵骇 Canon 鎽樿鍜?Story Bible銆?
### 楠岃瘉
- `pytest -q tests/runtime/test_chapter_workflow_task6.py`锛? passed銆?- `ruff check app/runtime/run_worker.py tests/runtime/test_chapter_workflow_task6.py`锛氶€氳繃銆?- `npm run typecheck`锛氶€氳繃銆?- `npx playwright test tests/chapter-workflow.spec.ts --list`锛氬垪鍑?2 鏉℃祴璇曘€?
### 褰撳墠涓嶈冻
-

## Task 6褰撳墠鐜杩愯 Playwright 娴忚鍣?Web Server 鏃惰繑鍥?`spawn EPERM`锛屽皻鏈幏寰楃湡瀹炴祻瑙堝櫒浜や簰璇佹嵁銆?- 鍓嶇浠撳簱娌℃湁 `npm run lint` 鑴氭湰锛屾湭鎵ц鍒?lint 鍛戒护銆?## Task 6 杩藉姞楠屾敹

### 鏈疆楠岃瘉
- 鍚庣鑱氱劍濂椾欢閫氳繃锛岃鐩?task4銆乼ask5銆乼ask6銆乼ask2銆丄PI 鍜?domain锛岀粨鏋滀负 31 passed銆?- Ruff 閫氳繃銆?- 鍓嶇绫诲瀷妫€鏌ラ€氳繃銆?- Playwright 娓呭崟鍙垪鍑?2 鏉＄敤渚嬨€?- 瀹為檯 Playwright 杩愯浠嶈褰撳墠鐜闃绘柇锛歴pawn EPERM銆?- 鍓嶇鐢熶骇鏋勫缓浠嶈褰撳墠鐜闃绘柇锛歴pawn EPERM銆?
### 褰撳墠鏈В鍐充簨椤?- 淇濈暀宸茬煡 minor锛氬垹闄ゅ綋鍓嶇珷鑺傚悗鏈竻鐞?selectedChapterId銆?- package.json 褰撳墠娌℃湁 lint script锛屽洜姝ゆ湭鎵ц npm run lint銆?




