# AF3 실행 조건 복구 — 2026-09-14

`Database component changed after installation validation: bfd-first_non_consensus_sequences.fasta` 오류의 원인은 숫자 장치 번호 변경이었습니다. 설치 당시 `66306`이 현재 `66307`로 바뀌었으며, DB 9종과 inventory의 크기·inode·수정 시각·변경 시각은 기존 기록과 일치했습니다. 현재 해당 볼륨의 파일시스템 UUID는 `8ddd80eb-6ff2-439e-bc5f-fa97b55fa26c`입니다.

기존 검증 기록을 바로 수정하지 않고 **195,877개 파일, 911,267,772,209바이트**를 실제로 읽어 SHA-256을 대조했습니다. FASTA 8개, 보관된 압축 원본 9개, PDB CIF 195,858개, inventory 1개, 보조 파일 1개가 모두 일치했습니다. 고정 다운로드 계획의 출처·generation·크기와 기존 CRC32C/MD5 검증 근거도 대조했습니다. 재다운로드나 압축 해제 없이 기존 데이터 본문을 보존했습니다. [전체 해시 검증 결과](af3-database-revalidation.json).

DB 설치기와 실행 전 검사가 같은 파일시스템 UUID를 사용하도록 수정했습니다. UUID 확인이 불가능한 환경에서는 숫자 장치 번호를 엄격하게 비교합니다. 크기·inode·mtime·ctime 검사도 유지합니다. 모델 파일과 검사 캐시의 식별 방식에도 같은 수정을 적용했습니다. 이전 형식의 기록은 전체 내용 재검증을 거쳐야 전환됩니다.

검증 전 manifest·구성별 receipt·압축 원본 receipt·inventory는 DB 디렉터리의 `.installation/revalidation-20260914T010834819791Z/`에 보존했습니다. 새 inventory와 receipt를 게시하고 manifest를 마지막에 갱신했습니다. 모든 파일 검사 후와 게시 전에 파일 상태를 다시 확인했으며, 기록과 백업의 디스크 동기화도 완료했습니다.

서버는 **0.0.0.0:9018**에서 재시작했습니다. 현재 `ready=true`, `runnable=true`, `blockers=[]`, DB 상태 `ready`이며, AF3 3.0.4·JAX 0.10.2의 `cuda:0` 점검을 통과했습니다. 마지막으로 차단됐던 성분과 COX-2 표적을 동일한 표준 MSA 조건·시드 1로 다시 준비했고, 새 작업 `1bf7e67cd1a34bb2b0dd0415a592db21`은 실행 가능한 `prepared` 상태입니다. 기존 실패 기록은 보존했습니다. 이번 복구 검증에서는 GPU 추론을 제출하지 않았습니다. [현재 실행 조건·입력 준비 결과](af3-readiness-repair-verification.json).

관련 자동 테스트 **240개**와 Python lint를 통과했습니다. 장치 번호만 바뀌었을 때 동일한 식별값 유지, 다른 파일시스템·내용 변경 시 차단, 같은 크기의 손상, 검사 중 교체, inventory 불일치, 동시 설치 잠금, 게시 중 중단과 백업 보존을 검증했습니다. [복구 명령과 운영 안내](af3-full-msa-setup.md).

실제 데스크톱·모바일 화면에서도 같은 성분의 새 작업이 **입력 준비됨**으로 표시되고, **이 성분 계산 시작** 버튼이 활성화되며 차단 조건이 표시되지 않는 것을 확인했습니다. 화면 검사 전후 작업 목록과 상태는 동일합니다. [화면 검증 결과](af3-readiness-repair-ui-verification.json) · [데스크톱 화면](images/af3-readiness-repaired-desktop.png) · [모바일 화면](images/af3-readiness-repaired-mobile.png).
