# V4 GA Documentation Cleanup Plan

**Purpose**: Align develop branch documentation with main branch structure for production release  
**Date**: October 29, 2025  
**Target**: staging → main merge for v4.0.0

---

## Table of Contents

1. [Documentation Audit](#documentation-audit)
2. [Main Branch Structure (Reference)](#main-branch-structure-reference)
3. [Keep - Production Ready](#keep---production-ready)
4. [Remove - Development Artifacts](#remove---development-artifacts)
5. [Create - New for GA v4.0](#create---new-for-ga-v40)
6. [Update - Existing Documents](#update---existing-documents)
7. [Execution Plan](#execution-plan)

---

## Documentation Audit

### Current Develop Branch (125 .md files)

**Root Directory** (25 files):
```
✅ README.md (update for v4.0)
✅ CONTRIBUTING.md (keep)
✅ V4-GA-MasterPlan.md (keep - GA reference)
✅ GA-CODE-CLEANUP.md (keep - GA reference)
✅ GA-CLEANUP-COMPLETE.md (keep - GA reference)

❌ Agents.md (remove - superseded by Transport-Mode-Compatibility)
❌ Gemini.md (remove - not production feature)
❌ BRIDGE_ARCHITECTURE_VERIFICATION.md (remove - dev artifact)
❌ CONFIG_V4_RELEASE.md (remove - dev artifact)
❌ DEPRECATED_CODE_AUDIT.md (remove - dev artifact)
❌ GAv4-CHECKLIST.md (remove - superseded by V4-GA-MasterPlan)
❌ PRODUCTION_HARDENING_PLAN.md (remove - dev artifact)
❌ OPTION3_IMPLEMENTATION_ANALYSIS.md (remove - dev artifact)
❌ ROADMAP_STATUS_20251025.md (remove - dev artifact)
❌ ROADMAP_STATUS_REVIEW_OCT27.md (remove - dev artifact)
❌ TESTING_GUIDE_P1.md (remove - dev artifact)
❌ OPENAI_*.md (8 files - remove, already removed most)
```

**docs/ Directory** (45 files):
```
✅ docs/README.md (keep)
✅ docs/Architecture.md (keep)
✅ docs/Configuration-Reference.md (keep)
✅ docs/INSTALLATION.md (keep)
✅ docs/Tuning-Recipes.md (keep)
✅ docs/FreePBX-Integration-Guide.md (keep)
✅ docs/Transport-Mode-Compatibility.md (keep - NEW in v4.0)
✅ docs/local-ai-server/PROTOCOL.md (keep)
✅ docs/case-studies/OPENAI_REALTIME_GOLDEN_BASELINE.md (keep - MOVED)

❌ docs/AudioSocket with Asterisk_ Technical Summary for A.md (remove - dev research)
❌ docs/AudioSocket-Provider-Alignment.md (remove - dev artifact)
❌ docs/BENCHMARKING-IMPLEMENTATION-SUMMARY.md (remove - dev artifact)
❌ docs/EXTERNAL_MEDIA_TEST_GUIDE.md (remove - dev testing)
❌ docs/ExternalMedia_Deployment_Guide.md (remove - superseded by INSTALLATION.md)
❌ docs/Hybrid-Pipeline-Golden-Baseline.md (remove - dev validation)
❌ docs/LOG_ANALYSIS_VAD_IMPLEMENTATION.md (remove - dev artifact)
❌ docs/Linear-Tracking-Rules.md (remove - internal process)
❌ docs/OPENAI_FORMAT_DISCOVERY.md (remove - dev research)
❌ docs/OpenAI-Realtime-Logging-Guide.md (remove - dev debugging)
❌ docs/Updated-Voice-AI-Stack.md (remove - dev artifact)
❌ docs/VAD_CRITICAL_FIXES_SUMMARY.md (remove - dev artifact)
❌ docs/VAD_IMPLEMENTATION_SUMMARY.md (remove - dev artifact)
❌ docs/baselines/golden/*.md (remove - dev validation, 3 files)
❌ docs/call-framework.md (remove - internal design)
❌ docs/deepgram-agent-api.md (remove - internal reference)
❌ docs/resilience.md (remove - incomplete)
❌ docs/regression/*.md (remove - dev testing, 4 files)
❌ docs/regressions/*.md (remove - dev testing, 4 files)
```

**docs/plan/ Directory** (5 files):
```
✅ docs/plan/ROADMAP.md (keep - merge with ROADMAPv4.md)
✅ docs/plan/CODE_OF_CONDUCT.md (keep)

❌ docs/plan/Asterisk AI Voice Agent_ Your Comprehensive Open Source Launch Strategy.md (remove - planning doc)
❌ docs/plan/P1_IMPLEMENTATION_PLAN.md (remove - dev artifact)
❌ docs/plan/README.md (remove - superseded)
❌ docs/plan/ROADMAPv4-GAP-ANALYSIS.md (remove - planning doc)
❌ docs/plan/ROADMAPv4.md (merge into ROADMAP.md, then remove)
```

**docs/milestones/ Directory** (4 files):
```
✅ docs/milestones/milestone-5-streaming-transport.md (keep)
✅ docs/milestones/milestone-6-openai-realtime.md (keep)
✅ docs/milestones/milestone-7-configurable-pipelines.md (keep)
✅ docs/milestones/milestone-8-monitoring-stack.md (keep)
```

**logs/ Directory** (50+ files):
```
❌ logs/remote/rca-*/*.md (remove ALL - dev RCA documents, ~40 files)
❌ logs/remote/golden-baseline-telephony-ulaw/*.md (remove - dev validation, 4 files)
❌ logs/test-call-logs-*.md (remove ALL - dev test logs, 8 files)
```

**Other Directories**:
```
✅ monitoring/README.md (keep)
✅ scripts/README.md (keep)
✅ tests/README.md (keep)
✅ tools/ide/README.md (keep)
```

---

## Main Branch Structure (Reference)

**Current main branch** (v3.0) has clean structure:

```
README.md
CONTRIBUTING.md
config/
  ├── ai-agent.example.yaml
  └── [profile-specific yamls]
docs/
  ├── README.md
  ├── Architecture.md
  ├── INSTALLATION.md
  ├── Configuration-Reference.md
  ├── Tuning-Recipes.md
  ├── FreePBX-Integration-Guide.md
  ├── local-ai-server/
  │   └── PROTOCOL.md
  ├── milestones/
  │   ├── milestone-5-streaming-transport.md
  │   ├── milestone-6-openai-realtime.md
  │   └── [2 more]
  └── plan/
      ├── ROADMAP.md
      └── CODE_OF_CONDUCT.md
monitoring/
  └── README.md
scripts/
  └── README.md
```

**No development artifacts in main** - clean and production-ready.

---

## Keep - Production Ready

### Root Directory (3 documents)

**✅ README.md** (UPDATE)
- Main entry point
- Update version to v4.0
- Add pipeline architecture highlights
- Update feature list

**✅ CONTRIBUTING.md** (KEEP)
- Contribution guidelines
- No changes needed

**✅ V4-GA-MasterPlan.md** (KEEP)
- Comprehensive GA release plan
- Reference document for v4.0
- Keep as historical record

### docs/ Core Documentation (6 documents)

**✅ docs/README.md** (KEEP)
- Documentation index
- No changes needed

**✅ docs/Architecture.md** (UPDATE)
- System architecture
- Add pipeline architecture section
- Update diagrams if needed

**✅ docs/INSTALLATION.md** (UPDATE)
- Installation guide
- Add monitoring stack setup
- Document pipeline selection

**✅ docs/Configuration-Reference.md** (UPDATE)
- Configuration reference
- Document new pipeline configs
- Clean up deprecated settings

**✅ docs/Tuning-Recipes.md** (UPDATE)
- Performance tuning
- Add pipeline-specific tuning

**✅ docs/FreePBX-Integration-Guide.md** (KEEP)
- FreePBX integration
- No changes needed

### docs/ New v4.0 Documentation (2 documents)

**✅ docs/Transport-Mode-Compatibility.md** (KEEP)
- Transport compatibility matrix
- Critical reference for v4.0
- Already well-documented

**✅ docs/case-studies/OPENAI_REALTIME_GOLDEN_BASELINE.md** (KEEP)
- OpenAI Realtime golden baseline
- Valuable production reference
- Keep in case-studies/

### docs/local-ai-server/ (1 document)

**✅ docs/local-ai-server/PROTOCOL.md** (KEEP)
- Local AI server protocol
- Updated and complete

### docs/milestones/ (4 documents)

**✅ All milestone documents** (KEEP)
- milestone-5-streaming-transport.md
- milestone-6-openai-realtime.md
- milestone-7-configurable-pipelines.md
- milestone-8-monitoring-stack.md
- Historical record of development

### docs/plan/ (2 documents)

**✅ docs/plan/ROADMAP.md** (MERGE & UPDATE)
- Merge ROADMAPv4.md content
- Update with v4.0 completion status
- Document v4.1+ future plans

**✅ docs/plan/CODE_OF_CONDUCT.md** (KEEP)
- Code of conduct
- No changes needed

### Other Documentation (4 documents)

**✅ monitoring/README.md** (KEEP)
- Monitoring stack documentation
- Complete and production-ready

**✅ scripts/README.md** (KEEP)
- Scripts documentation
- No changes needed

**✅ tests/README.md** (KEEP)
- Testing documentation
- No changes needed

**✅ tools/ide/README.md** (KEEP)
- IDE tools documentation
- No changes needed

---

## Remove - Development Artifacts

### Root Directory (19 documents)

**❌ Development Process Documents**:
```bash
rm Agents.md  # Superseded by Transport-Mode-Compatibility.md
rm Gemini.md  # Not a production feature
rm BRIDGE_ARCHITECTURE_VERIFICATION.md
rm CONFIG_V4_RELEASE.md
rm DEPRECATED_CODE_AUDIT.md
rm GAv4-CHECKLIST.md  # Superseded by V4-GA-MasterPlan.md
rm PRODUCTION_HARDENING_PLAN.md
rm OPTION3_IMPLEMENTATION_ANALYSIS.md
rm ROADMAP_STATUS_20251025.md
rm ROADMAP_STATUS_REVIEW_OCT27.md
rm TESTING_GUIDE_P1.md
```

**❌ RCA Documents** (if any remain):
```bash
rm OPENAI_*.md  # Already removed most, check for any remaining
```

**Note**: Keep GA-CODE-CLEANUP.md and GA-CLEANUP-COMPLETE.md as v4.0 release references.

### docs/ Directory (22 documents)

**❌ Development Research & Analysis**:
```bash
rm docs/AudioSocket\ with\ Asterisk_\ Technical\ Summary\ for\ A.md
rm docs/AudioSocket-Provider-Alignment.md
rm docs/BENCHMARKING-IMPLEMENTATION-SUMMARY.md
rm docs/EXTERNAL_MEDIA_TEST_GUIDE.md
rm docs/ExternalMedia_Deployment_Guide.md  # Superseded by INSTALLATION.md
rm docs/Hybrid-Pipeline-Golden-Baseline.md
rm docs/LOG_ANALYSIS_VAD_IMPLEMENTATION.md
rm docs/OPENAI_FORMAT_DISCOVERY.md
rm docs/OpenAI-Realtime-Logging-Guide.md
rm docs/Updated-Voice-AI-Stack.md
rm docs/VAD_CRITICAL_FIXES_SUMMARY.md
rm docs/VAD_IMPLEMENTATION_SUMMARY.md
rm docs/call-framework.md
rm docs/deepgram-agent-api.md
rm docs/resilience.md  # Incomplete
```

**❌ Development Validation**:
```bash
rm -rf docs/baselines/  # Entire directory (3 files)
rm -rf docs/regression/  # Entire directory (1 file)
rm -rf docs/regressions/  # Entire directory (4 files)
```

**❌ Internal Process**:
```bash
rm docs/Linear-Tracking-Rules.md  # Internal Linear workflow
```

### docs/plan/ Directory (4 documents)

**❌ Planning Documents**:
```bash
rm docs/plan/Asterisk\ AI\ Voice\ Agent_\ Your\ Comprehensive\ Open\ Source\ Launch\ Strategy.md
rm docs/plan/P1_IMPLEMENTATION_PLAN.md
rm docs/plan/README.md  # Superseded
rm docs/plan/ROADMAPv4-GAP-ANALYSIS.md
# Note: Merge ROADMAPv4.md into ROADMAP.md first, then:
rm docs/plan/ROADMAPv4.md
```

### logs/ Directory (50+ documents)

**❌ ALL RCA and Test Logs**:
```bash
rm -rf logs/remote/  # Entire directory (~44 RCA documents)
rm logs/test-call-logs-*.md  # All test call logs (8 files)
```

**Justification**: Logs are for development troubleshooting. All critical findings documented in:
- docs/case-studies/OPENAI_REALTIME_GOLDEN_BASELINE.md
- docs/Transport-Mode-Compatibility.md
- Code comments

### Total Files to Remove: ~75 files

---

## Create - New for GA v4.0

### Root Directory (1 document)

**📝 CHANGELOG.md** (NEW)
- v4.0.0 release notes
- Breaking changes (none)
- New features
- Bug fixes
- Migration guide

**Template**:
```markdown
# Changelog

## [4.0.0] - 2025-10-30

### 🎉 Major Features
- Modular pipeline architecture
- Mix local and cloud AI components
- Production monitoring stack

### ✅ Validated Configurations
- local_hybrid (recommended)
- hybrid_support (cloud)
- local_only (air-gapped)

### 🔧 Improvements
- Enhanced transport layer
- Pipeline codec management
- Production logging

### 📚 Documentation
- Hardware requirements
- Monitoring guide
- Transport compatibility

### ⚠️ Breaking Changes
None - backward compatible with v3.x

See V4-GA-MasterPlan.md for complete details.
```

### docs/ Directory (3 documents)

**📝 docs/HARDWARE_REQUIREMENTS.md** (NEW)
- Hardware specs for each pipeline
- Performance benchmarks
- CPU vs GPU guidance
- Cost analysis

**📝 docs/MONITORING_GUIDE.md** (NEW)
- Prometheus + Grafana setup
- Dashboard descriptions
- Alert configuration
- Troubleshooting with metrics

**📝 docs/PRODUCTION_DEPLOYMENT.md** (NEW)
- Production best practices
- Security hardening
- Backup procedures
- Upgrade process

### docs/ Directory (1 document)

**📝 docs/TESTING_VALIDATION.md** (NEW)
- Pipeline validation results
- Test call summaries
- Performance metrics
- Known limitations

---

## Update - Existing Documents

### Root Directory

**📝 README.md**
- Update version: v3.0 → v4.0
- Add GA v4.0 badge
- Update feature list:
  - Add: Modular pipeline architecture
  - Add: Production monitoring stack
  - Add: Local + cloud hybrid support
- Update quick start for pipeline selection
- Add links to new docs

### docs/ Directory

**📝 docs/Architecture.md**
- Add pipeline architecture section
- Document STT → LLM → TTS flow
- Update architecture diagram
- Explain transport layer

**📝 docs/INSTALLATION.md**
- Add monitoring stack setup section
- Document pipeline selection
- Add hardware requirements reference
- Update troubleshooting

**📝 docs/Configuration-Reference.md**
- Document pipeline configuration
- Remove deprecated settings mention
- Add transport compatibility reference
- Update examples

**📝 docs/Tuning-Recipes.md**
- Add pipeline-specific tuning
- Document local_only hardware optimization
- Add monitoring integration

**📝 docs/plan/ROADMAP.md**
- Merge ROADMAPv4.md content
- Mark v4.0 as complete
- Document v4.1+ plans
- Clean up completed items

---

## Execution Plan

### Phase 1: Remove Development Artifacts (30 min)

```bash
# Root directory cleanup
cd /Users/haider.jarral/Documents/Claude/Asterisk-AI-Voice-Agent

# Remove dev documents
rm Agents.md Gemini.md BRIDGE_ARCHITECTURE_VERIFICATION.md \
   CONFIG_V4_RELEASE.md DEPRECATED_CODE_AUDIT.md GAv4-CHECKLIST.md \
   PRODUCTION_HARDENING_PLAN.md OPTION3_IMPLEMENTATION_ANALYSIS.md \
   ROADMAP_STATUS_20251025.md ROADMAP_STATUS_REVIEW_OCT27.md \
   TESTING_GUIDE_P1.md

# Check for any remaining OPENAI_ files
ls OPENAI_*.md 2>/dev/null && rm OPENAI_*.md

# Remove docs/ artifacts
rm docs/AudioSocket\ with\ Asterisk_\ Technical\ Summary\ for\ A.md \
   docs/AudioSocket-Provider-Alignment.md \
   docs/BENCHMARKING-IMPLEMENTATION-SUMMARY.md \
   docs/EXTERNAL_MEDIA_TEST_GUIDE.md \
   docs/ExternalMedia_Deployment_Guide.md \
   docs/Hybrid-Pipeline-Golden-Baseline.md \
   docs/LOG_ANALYSIS_VAD_IMPLEMENTATION.md \
   docs/OPENAI_FORMAT_DISCOVERY.md \
   docs/OpenAI-Realtime-Logging-Guide.md \
   docs/Updated-Voice-AI-Stack.md \
   docs/VAD_CRITICAL_FIXES_SUMMARY.md \
   docs/VAD_IMPLEMENTATION_SUMMARY.md \
   docs/call-framework.md \
   docs/deepgram-agent-api.md \
   docs/resilience.md \
   docs/Linear-Tracking-Rules.md

# Remove entire directories
rm -rf docs/baselines/ docs/regression/ docs/regressions/

# Remove docs/plan/ artifacts  
rm docs/plan/Asterisk\ AI\ Voice\ Agent_\ Your\ Comprehensive\ Open\ Source\ Launch\ Strategy.md \
   docs/plan/P1_IMPLEMENTATION_PLAN.md \
   docs/plan/README.md \
   docs/plan/ROADMAPv4-GAP-ANALYSIS.md

# Remove logs
rm -rf logs/remote/
rm logs/test-call-logs-*.md

# Git commit
git add -A
git commit -m "docs: Remove development artifacts for GA v4.0

Removed ~75 development documentation files:
- Dev process documents (11 files)
- Dev research and analysis (14 files)
- Planning documents (4 files)
- RCA and test logs (50+ files)
- Incomplete/superseded docs

All critical information preserved in:
- Production documentation
- Case studies
- Code comments

Clean repository for GA release"
```

### Phase 2: Create New Documentation (4 hours)

**Order of creation**:
1. CHANGELOG.md (30 min)
2. docs/HARDWARE_REQUIREMENTS.md (1 hour)
3. docs/MONITORING_GUIDE.md (1 hour)
4. docs/PRODUCTION_DEPLOYMENT.md (1 hour)
5. docs/TESTING_VALIDATION.md (30 min)

### Phase 3: Update Existing Documentation (2 hours)

**Order of updates**:
1. README.md (30 min)
2. docs/Architecture.md (30 min)
3. docs/INSTALLATION.md (30 min)
4. docs/Configuration-Reference.md (15 min)
5. docs/Tuning-Recipes.md (15 min)

### Phase 4: Merge ROADMAP (30 min)

1. Merge ROADMAPv4.md content into docs/plan/ROADMAP.md
2. Mark v4.0 complete
3. Document v4.1+ plans
4. Remove ROADMAPv4.md

### Phase 5: Final Review (1 hour)

- [ ] Check all links work
- [ ] Verify no broken references
- [ ] Review for consistency
- [ ] Check formatting
- [ ] Test examples

### Phase 6: Commit and Push

```bash
git add -A
git commit -m "docs: Complete GA v4.0 documentation

Created:
- CHANGELOG.md
- docs/HARDWARE_REQUIREMENTS.md
- docs/MONITORING_GUIDE.md
- docs/PRODUCTION_DEPLOYMENT.md
- docs/TESTING_VALIDATION.md

Updated:
- README.md (v4.0)
- docs/Architecture.md
- docs/INSTALLATION.md
- docs/Configuration-Reference.md
- docs/Tuning-Recipes.md
- docs/plan/ROADMAP.md

Total: 5 new + 6 updated documents
Status: Ready for production"

git push origin develop
```

---

## Summary

### Files by Action

| Action | Count | Time |
|--------|-------|------|
| **Remove** | ~75 files | 30 min |
| **Create** | 5 files | 4 hours |
| **Update** | 6 files | 2 hours |
| **Keep** | 23 files | - |
| **Total** | 109 files | 6.5 hours |

### Final Structure

**Production-ready documentation**:
- Clean root directory (4 core docs)
- Organized docs/ structure (14 user-facing docs)
- Complete monitoring/ docs (1 file)
- Preserved milestones (4 files)
- Clean plan/ directory (2 files)
- Supporting README files (4 files)

**Total**: 29 production documents

---

## Validation Checklist

Before merge to staging:

- [ ] All development artifacts removed
- [ ] All new documentation created
- [ ] All existing documentation updated
- [ ] All links verified
- [ ] Examples tested
- [ ] Formatting consistent
- [ ] No broken references
- [ ] Git history clean

---

**Status**: 🟢 READY TO EXECUTE  
**Timeline**: 6.5 hours total  
**Risk**: Low (all changes reversible via git)  
**Impact**: Clean, professional documentation for GA v4.0
