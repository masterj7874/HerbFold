**AlphaFold 3 전체 MSA·템플릿 데이터베이스 설치 완료 기록**

2026-09-08 01:40:18 UTC(한국 시각 10:40:18)에 공식 데이터베이스 9종의 설치와 독립 사후 검사를 완료했습니다. 백엔드 준비 검사는 `status=ready`, `runnable=true`, 차단 사유 없음으로 확인됐습니다. 이 문서는 **데이터베이스 설치 완료**를 기록합니다. 실제 표적의 MSA 검색, 검색 자료를 사용한 AF3 추론, 구조 신뢰도 비교의 완료 결과는 포함하지 않습니다. [실제 설치·독립 검사 보고서](af3-full-database-installation.json)

설치 위치와 서버의 `AF3_DB_DIR` 설정값은 다음과 같습니다. 완료 manifest는 이 디렉터리의 `official_databases_manifest.json`입니다.

```text
/media/jerisuh/8ddd80eb-6ff2-439e-bc5f-fa97b55fa26c/alphafold3_databases/v3.0
```

배포 기준은 AF3 **v3.0.4**, commit `85c4d20505fd5cef05eac22b534d4e793971ae69`의 공식 `fetch_databases.sh`입니다. AF3 코드 버전과 데이터베이스 배포 경로의 `v3.0`은 서로 다른 버전 표기입니다. 단백질 MSA용 Small BFD·MGnify·UniRef90·UniProt, RNA MSA용 NT-RNA·RNAcentral·Rfam, 템플릿용 PDB seqres·mmCIF를 모두 설치했습니다. 단백질 전용 입력에서도 공식 실행기가 모든 데이터베이스 경로를 확인하므로 RNA 파일을 생략하지 않았습니다. [공식 다운로드 목록](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/fetch_databases.sh), [공식 실행기](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/run_alphafold.py), [검증한 URL·generation·크기 계획](af3-msa-db-plan.json)

아래 수는 실제 설치 과정에서 센 FASTA 레코드 또는 CIF 파일 수입니다. 서로 다른 데이터베이스 간 중복을 제거한 서열 수나 특정 표적의 MSA 검색 결과 수를 뜻하지 않습니다. 용량은 모두 정확한 바이트 수입니다.

| 공식 구성 | 실제 레코드/CIF 수 | 압축 원본 바이트 | 설치된 본문 바이트 |
| --- | ---: | ---: | ---: |
| Small BFD, first non-consensus | 65,984,053 | 9,880,478,127 | 18,171,626,364 |
| MGnify, 2022-05 | 623,796,864 | 69,333,008,025 | 128,579,703,018 |
| UniRef90, 2022-05 | 153,742,194 | 33,271,639,594 | 71,821,260,491 |
| UniProt, 2021-04 | 225,619,586 | 48,688,827,585 | 108,447,942,931 |
| NT-RNA, 2023-02-23, 90% identity/80% coverage | 37,105,891 | 17,028,641,580 | 80,977,012,680 |
| RNAcentral active, 90% identity/80% coverage | 15,630,712 | 3,519,676,108 | 13,860,314,914 |
| Rfam 14.9, 90% identity/80% coverage | 871,599 | 56,604,542 | 228,433,680 |
| PDB seqres, 2022-09-28 | 692,804 | 26,493,534 | 232,899,463 |
| PDB mmCIF, 2022-09-28 | 195,858 | 56,979,074,571 | 250,115,836,927 |
| **합계** | **FASTA 8종 + CIF 트리 1종** | **238,784,443,666** | **672,435,030,468** |

압축 원본은 약 238.784 GB(222.385 GiB), 설치 본문은 약 672.435 GB(626.254 GiB)입니다. 둘을 합한 본문 용량은 약 848.639 GiB이며 manifest, inventory, 설치 기록, 파일시스템 메타데이터와 임시 작업 공간은 별도입니다. PDB 설치 본문에는 tar 헤더·패딩과 아래 45바이트 보조 파일을 포함하지 않습니다. 따라서 초기 zstd 프레임에 기재된 tar 전체 해제 크기와 실제 CIF 본문 합계는 다릅니다.

설치 디렉터리 안의 정확한 입력 경로는 다음과 같습니다. 압축 원본은 `.downloads/`에 보존하며, FASTA는 같은 이름 뒤에 `.zst`가 붙은 객체에서 해제했습니다. PDB 원본은 `pdb_2022_09_28_mmcif_files.tar.zst`입니다.

```text
bfd-first_non_consensus_sequences.fasta
mgy_clusters_2022_05.fa
uniref90_2022_05.fa
uniprot_all_2021_04.fa
nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta
rnacentral_active_seq_id_90_cov_80_linclust.fasta
rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta
pdb_seqres_2022_09_28.fasta
mmcif_files/
```

검증은 세 단계로 나뉩니다.

| 단계 | 실제 확인한 범위 | 해당 단계에서 반복하지 않는 검사 |
| --- | --- | --- |
| 설치기의 다운로드·해제 | 공식 URL과 고정 generation, 실제 전량 크기, Google CRC32C·MD5, 전량 SHA-256 기록. 해제 중 압축 원본 해시를 다시 대조하고 zstd 프레임·체크섬·EOF·선언 크기를 확인. FASTA 헤더·비어 있지 않은 서열·문자·레코드 집계, CIF 경로·일반 파일 여부·본문 시작·크기·각 본문 SHA-256 및 inventory 생성 | 설치된 모든 본문을 다시 여는 별도 두 번째 디스크 전체 해시 패스는 아님 |
| 독립 설치 후 검사 | 공식 계획과 9종 receipt 대조, 압축/설치 파일 상태, 전체 PDB inventory 해시, CIF 195,858개 각각의 경로·크기·inode·수정/변경 시각과 목록 일치, 보조 파일 실제 45바이트·해시 | 약 911 GB의 압축 및 설치 본문 전체를 다시 해시하지 않음 |
| 백엔드 요청 시 준비 검사 | 완료 manifest의 버전·commit·9종 구성·검증 기록·해시 형식, FASTA와 PDB 루트 및 inventory의 파일 상태. manifest SHA와 경로·파일 상태를 합친 설치 지문 생성 | 매 요청마다 전체 본문 해시나 개별 CIF 195,858개의 상태를 순회하지 않음 |

FASTA의 `sha256`은 해제된 파일 본문의 해시입니다. PDB 트리의 최상위 `sha256`은 `pdb_inventory.jsonl`의 해시이며, 각 CIF 본문 해시는 그 inventory의 각 행에 따로 있습니다. 백엔드 응답의 `full_contents_rehashed_this_check=false`는 위 범위 차이를 명시합니다. 준비 상태가 참이어도 이후 표적별 검색·추론 성공이나 구조 정확도가 보장되는 것은 아닙니다. [설치기](../scripts/install_af3_databases.py), [준비 검사 구현](../src/herbfold/af3_databases.py)

공식 PDB tar에는 CIF 외에 `mmcif_files/mmcif_files.tar.gz`라는 **45바이트 일반 파일 한 개**가 들어 있었습니다. 전체 tar 195,860개 항목을 읽어 루트 디렉터리 1개, CIF 195,858개, 이 보조 파일 1개임을 확인했습니다. 심볼릭 링크·하드 링크·다른 예외는 없었습니다. 설치기는 공식 원본 URL, generation `1730832728463652`, 정확한 경로, 일반 파일 유형, 45바이트 크기와 아래 SHA-256이 모두 일치할 때만 이 파일을 템플릿 목록에서 제외합니다.

```text
SHA-256: 85cea451eec057fa7e734548ca3ba6d779ed5836a3f9de14b8394575ef0d7d8e
보관 경로: .installation/ancillary/mmcif_files.tar.gz
```

45바이트 원본은 그대로 보관했고 내부 압축물을 추가로 풀지 않았습니다. PDB receipt의 `excluded_ancillary_members`에 출처·원래 경로·보관 경로·크기·해시·파일 상태를 기록하고 재사용 때 확인합니다. 다른 비-CIF 항목, 경로 이탈, 링크는 계속 거부합니다. [전체 tar 검사](af3-pdb-archive-member-audit.json), [45바이트 증거](af3-pdb-ancillary-member.json), [수정·86개 격리 테스트 기록](af3-pdb-installer-correction.json)

재현 또는 중단 후 재개는 저장소 루트에서 같은 명령을 사용합니다. CRC32C·zstd 의존성은 `databases` extra와 `uv.lock`에 고정돼 있습니다.

```bash
uv run --locked --extra databases python scripts/install_af3_databases.py \
  --plan docs/af3-msa-db-plan.json \
  --directory /media/jerisuh/8ddd80eb-6ff2-439e-bc5f-fa97b55fa26c/alphafold3_databases/v3.0
```

이 명령은 설치·재개 명령이며 GPU 추론을 실행하지 않습니다. 같은 설치 위치에 중복 실행하지 않도록 설치기가 잠금을 사용합니다. 진행 상태는 `.installation/status.json`, 구성별 완료 기록은 `.installation/*.expanded.json`, 압축 원본 기록은 `.downloads/*.receipt.json`에 남습니다. 다운로드 중단 시 실제 `.partial` 바이트를 다시 해시한 뒤 이어받고, 완성된 검증 원본과 버전 2 설치 receipt가 현재 파일 상태와 일치하면 재사용합니다. 미완료 해제 작업은 설치기 전용 `.expanding` 자료만 다시 만듭니다. 이 명령을 다시 실행한다고 완료된 모든 본문을 항상 전량 재해시하는 것은 아닙니다.

운영 중에는 데이터베이스 본문·inventory·manifest를 고정된 한 세트로 보존합니다. 파일 내용을 수정하거나 외부 도구로 교체한 뒤 기존 receipt를 수동으로 고쳐서 재사용하지 않습니다. 교체가 필요하면 기존 근거를 보존하고 새 설치 위치에서 전체 설치 검증을 마친 뒤 `AF3_DB_DIR`을 변경하고 서버를 재시작합니다. 내용 손상이 의심되는 경우에는 가벼운 준비 검사만으로 복구 완료를 판단하지 않고 전체 본문 검증 또는 검증 원본으로 재설치를 수행합니다. 완료 manifest와 단순 파일 상태만으로는 모든 저장장치 손상을 감지할 수 없습니다.

**장치 번호 변경으로 인한 준비 오류 복구:** 이전 receipt는 Linux의 숫자 장치 번호까지 비교했기 때문에 재부팅·장치 재연결 후 파일 내용이 그대로여도 `Database component changed after installation validation`이 발생할 수 있었습니다. 새 설치 기록은 확인 가능한 파일시스템 UUID를 사용하고, 크기·inode·수정/변경 시각 검사는 유지합니다. UUID를 확인할 수 없는 환경에서는 기존 장치 번호 검사를 사용합니다. 기존 기록은 자동으로 신뢰하지 않으며 다음 명령으로 전체 내용을 재검증한 뒤 전환합니다.

```bash
uv run --locked python scripts/revalidate_af3_databases.py \
  --directory /media/jerisuh/8ddd80eb-6ff2-439e-bc5f-fa97b55fa26c/alphafold3_databases/v3.0 \
  --plan docs/af3-msa-db-plan.json \
  --workers 4 \
  --report docs/af3-database-revalidation.json
```

이 명령은 보관된 압축 원본 9개, FASTA 8개, PDB inventory와 모든 CIF 본문, 보조 파일의 전체 SHA-256을 기존 검증 기록과 대조합니다. 고정 다운로드 계획의 출처·세대·크기·체크섬 근거도 확인합니다. 파일 내용이 달라지거나 검사 중 교체되면 완료 기록을 게시하지 않습니다. 검증을 통과한 경우 기존 기록과 inventory를 `.installation/revalidation-시각/`에 백업하고, 새 inventory와 receipt를 만든 뒤 manifest를 마지막에 게시합니다. 데이터 본문을 바꾸거나 다운로드·AF3 추론을 실행하지 않습니다. 전환 후 서버를 재시작하고, 이전에 차단된 계산은 **같은 조건으로 다시 준비**해 현재 설치 지문을 반영합니다.

단백질 MSA·템플릿 캐시는 `HERBFOLD_DATA_DIR/af3-protein-features/{key}/`에 별도로 저장합니다. 전체 단백질 서열 SHA, DB 설치 지문, AF3 버전·commit·실행기 또는 고정 이미지, HMMER 실행 파일 상태, 최대 템플릿 날짜와 캐시 스키마가 같은 경우에만 재사용합니다. 성공 종료한 검색의 실제 `*_data.json`에서 서열·A3M 정렬·템플릿 좌표와 매핑을 확인한 뒤 원자적으로 게시하며, 재사용할 때 feature 본문 SHA와 내용을 다시 검사합니다. 손상·미완료 캐시는 사용하지 않습니다. 리간드·seed는 단백질 캐시 키에 포함하지 않지만 각 물질의 별도 추론 입력과 작업 식별에는 유지합니다. 새 DB 설치 지문은 기존 캐시와 다른 키를 만듭니다. [검색·캐시 실행 계약](af3-calculation-workflow.md), [캐시 구현](../src/herbfold/af3_features.py)

이 설치 기록만으로 MSA를 사용한 구조 신뢰도 향상이나 결합·약효를 판단하지 않습니다. 이후 완료한 실제 검색과 아스피린·퀘르세틴의 별도 추론에서 관측한 MSA·템플릿 수, 출처와 캐시 사용, 원자·서열 일치 및 이전 계산과의 비교는 [실제 MSA 검증 보고서](af3-msa-validation.md)에 별도로 기록했습니다. 구조 신뢰도 상승은 결합 자세·약효·안전성 검증을 뜻하지 않습니다.
