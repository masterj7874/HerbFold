# 한글 한약재명 검색 연결

2026-09-07 [한국한의약진흥원 한의약소재은행의 공개 보유 한약재 색인](https://nikom.or.kr/kmmb/module/herbs/index.do)에서 화면에 제공하는 70개 페이지, 691개 행을 확인했습니다. 원천의 한약재명과 학명이라는 사실 정보만 검색 연결에 사용합니다. 효능 설명이나 본문 자료는 프로그램 데이터에 복제하지 않습니다.

570개 이름·학명 쌍을 채택하여 **서로 다른 한글 검색명 569개**를 제공합니다. 같은 이름인 모과가 두 학명으로 제시되어 있으므로 행 수와 검색명 수가 다릅니다. 나머지 121개 행에는 학명이 비어 있는 77개 행, 추가 대조가 필요한 표기·소재 27개, 기타 광물명·미해결 표기 등이 포함됩니다. 처방명을 하나의 생물종으로 바꾸거나 의심되는 철자를 임의로 고치지 않았습니다.

이 데이터는 공개 원천에 보고된 분류명입니다. 최신 인정 학명을 모두 대조한 목록, 모든 공정서 기원의 완전한 목록, 실제 한약재 배치의 성분을 확인한 자료는 아닙니다. 동물·진균·식품 등 색인에 포함된 생물성 소재도 존재합니다. 각 결과에 이 범위와 정확한 출처 URL을 표시합니다. 원천 사이트에 개방형 콘텐츠 라이선스를 임의로 부여하지 않으며, 메타데이터에는 `unspecified; factual reference index only`를 저장합니다.

## 검색 동작

`src/herbfold/herb_aliases.py`의 `resolve_herb_query`는 공백을 정리한 **정확한 한글 이름**만 학명으로 연결합니다. 한글 유니코드 NFC를 정규화하지만 복합 검색어를 임의로 해석하지 않습니다. 등록되지 않은 이름이나 이미 입력한 라틴 학명은 그대로 유지합니다.

| 입력 | 검색어 | 범위 |
| --- | --- | --- |
| 황금 | Scutellaria baicalensis | 원천이 보고한 종 |
| 인삼 | Panax ginseng | 원천이 보고한 종 |
| 황기 | Astragalus membranaceus | 원천이 보고한 종 |
| 당귀 | Angelica gigas | 원천이 보고한 종 |
| 강황 | Curcuma longa | 원천이 보고한 종 |
| 지모 | Anemarrhena asphodeloides | 원천이 보고한 종 |
| 감초 | Glycyrrhiza | 여러 기원을 보존한 속 단위 검색 |
| 황련 | Coptis | 여러 기원을 보존한 속 단위 검색 |
| 모과 | Chaenomeles | 색인의 두 기원을 보존한 속 단위 검색 |
| 갈근 성분 | 갈근 성분 | 등록명과 정확히 일치하지 않으므로 유지 |

감초는 [한국한의학연구원 OASIS 기원 정보](https://oasis.kiom.re.kr/oasis/herb/monoDetailView_M01.jsp?idx=7&keyword=&srch_menu_nix=&tab=1&work_seq=null)의 `G. uralensis`, `G. glabra`, `G. inflata`를 모두 보존합니다. 황련은 [진흥원 색인](https://nikom.or.kr/kmmb/module/herbs/index.do?viewPage=61)의 `C. japonica`와 기존에 확인한 [C. chinensis 원논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC8166882/)을 함께 보존합니다. 모과는 [같은 공개 색인](https://nikom.or.kr/kmmb/module/herbs/index.do?viewPage=16)의 `Chaenomeles speciosa`, `C. sinensis` 두 항목을 보존합니다.

같은 속의 여러 기원을 하나만 골라 버리지 않고 속 전체로 검색하므로, 원천에 열거된 기원 외의 다른 종도 결과에 포함될 수 있습니다. 이 경우 `scope="broader_genus"`와 설명을 반환합니다. 다른 속이 혼재해 단일 검색어를 정할 수 없는 경우에는 임의로 합치지 않는 동작도 준비되어 있습니다.

반환값에는 `query`, `original_query`, `mapped`, `taxa`, `scope`, `scope_note`, `mapping_source_url`, `source_urls`, `license_label`이 포함됩니다. `list_herb_aliases()`는 같은 근거를 포함한 이름별 목록을 반환합니다. 호출자가 반환 객체를 변경해도 등록된 원본 데이터는 바뀌지 않습니다.

## 수집 근거와 검증

`runtime/discovery/downloads/`에 다음 자료를 보존했습니다.

- `nikom-herbs-index-page1.html`부터 `page70.html`까지 공개 응답 및 각 파일의 URL·SHA256 영수증.
- `nikom-herb-name-facts.json`: 확인한 691개 이름·학명 행과 정확한 페이지 URL.
- `nikom-herb-alias-exclusions.json`: 추가 대조가 필요해 제외한 27개 이름과 이유.
- `nikom-herb-alias-manifest.json`: 채택 수, 요청 수, 범위, 모듈 해시.

기존에 받은 페이지는 재사용하고 순차 요청 간격은 최소 1.1초로 설정했습니다. 초기 웹 확인을 포함해 총 71회 요청했으며 로그인이나 접근 제한을 우회하지 않았습니다.

`tests/test_herb_aliases.py`의 네 테스트가 정확한 이름 연결, 미등록·복합 검색어 보존, 여러 기원의 명시적 확대, 핵심 약재 연결, 반환 데이터 격리와 제외 항목을 확인합니다. 카탈로그 테스트를 포함한 관련 18개 테스트와 Ruff 검사가 통과했습니다.
