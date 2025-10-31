# 🚀 Deployment Guide - Production Server

## 📋 **Pre-Deployment Checklist**

### **Server Info:**
- **Server:** root@voiprnd.nemtclouddispatch.com
- **Project Path:** `/root/Asterisk-AI-Voice-Agent`
- **Current Branch:** `develop`

---

## 🔄 **Deployment Steps**

### **1. SSH into Production Server**

```bash
ssh root@voiprnd.nemtclouddispatch.com
cd /root/Asterisk-AI-Voice-Agent
```

---

### **2. Check Current Status**

```bash
# Check current branch and commit
git branch
git log --oneline -3

# Check running containers
docker ps
```

---

### **3. Backup Current State**

```bash
# Create backup of current configs
mkdir -p ~/backups/$(date +%Y%m%d_%H%M%S)
cp config/ai-agent.yaml ~/backups/$(date +%Y%m%d_%H%M%S)/
cp config/contexts/*.yaml ~/backups/$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true

# Optional: Backup session data if needed
docker exec ai-engine tar czf /tmp/sessions_backup.tar.gz /app/data 2>/dev/null || true
docker cp ai-engine:/tmp/sessions_backup.tar.gz ~/backups/$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true
```

---

### **4. Pull Latest Changes from Develop**

```bash
# Stash any local changes (if any)
git stash

# Pull latest develop branch
git fetch origin develop
git checkout develop
git pull origin develop

# Verify you got the latest commit
git log --oneline -3
```

---

### **5. Review Configuration Changes**

```bash
# Check for any config file changes
git diff HEAD~1 config/

# If configs changed, verify your custom settings are preserved
cat config/ai-agent.yaml | grep -A5 "contexts:"
```

---

### **6. Rebuild and Restart Containers**

```bash
# Stop current containers
docker-compose down

# Rebuild with latest code
docker-compose build --no-cache ai-engine

# Start containers
docker-compose up -d

# Verify containers are running
docker ps
```

---

### **7. Verify Deployment**

```bash
# Check ai-engine logs for startup
docker logs ai-engine --tail 50

# Look for successful startup messages:
# - "AI Engine initialized"
# - "Pipeline runner started"
# - "ARI client connected"

# Test a quick call to verify personalized greetings work
# Call: (925) 736-6718 and press 8 for Local Hybrid
# Expected: "Hi [YourName]!" (if CALLERID is set)
```

---

### **8. Monitor for Issues**

```bash
# Watch logs in real-time during test calls
docker logs -f ai-engine

# Check for any errors related to:
# - Template substitution
# - Greeting synthesis
# - TTS "star star" issues (should be fixed)
```

---

## 🔥 **Quick Rollback (If Needed)**

```bash
# Stop containers
docker-compose down

# Rollback to previous commit
git log --oneline -5  # Find previous working commit
git reset --hard <previous-commit-hash>

# Restore backed up configs if needed
cp ~/backups/YYYYMMDD_HHMMSS/ai-agent.yaml config/

# Restart
docker-compose up -d
```

---

## 🧪 **Testing Checklist**

After deployment, test these scenarios:

### **1. Personalized Greeting Test**
- [ ] Call with valid CALLERID(name) set
- [ ] Verify agent says "Hi [YourName]!" instead of "Hi !"
- [ ] Test with empty CALLERID (should say "Hi there!")

### **2. Star Star Fix Test**
- [ ] Call demo_project_expert context
- [ ] Ask: "What are the key features?"
- [ ] Verify NO "star star" in response
- [ ] Should hear clean: "KEY FEATURES:" not "star star KEY FEATURES star star"

### **3. All Pipeline Tests**
- [ ] Press 6: Deepgram (enterprise cloud)
- [ ] Press 7: OpenAI Realtime (modern cloud)
- [ ] Press 8: Local Hybrid (privacy-focused with caller name)

---

## 📝 **New Features in This Deployment**

### **1. Personalized Greetings with Caller Name**
- Greetings now support `{caller_name}` and `{caller_number}` placeholders
- Automatically substituted from Asterisk CALLERID variables
- Example: "Hi John!" instead of generic "Hi!"
- Fallback to "there" if name not provided

**Updated Greeting:**
```yaml
demo_hybrid:
  greeting: "Hi {caller_name}! I'm Ava running on a local hybrid pipeline..."
```

### **2. Fixed TTS "Star Star" Issue**
- Removed all markdown bold formatting (`**text**`) from system prompts
- TTS was literally saying "star star" when reading prompts
- All prompts now use plain text headings

**Before:** `**KEY FEATURES:**` → TTS: "star star KEY FEATURES star star"  
**After:** `KEY FEATURES:` → TTS: "KEY FEATURES"

---

## 🔍 **Troubleshooting**

### **Issue: Containers won't start**
```bash
# Check for port conflicts
netstat -tulpn | grep -E ':(8088|8001|9090|3000)'

# Check docker logs
docker-compose logs ai-engine
```

### **Issue: Greeting doesn't use caller name**
```bash
# Check if caller_name is captured
docker logs ai-engine | grep "caller_name"

# Verify dialplan sets CALLERID(name) BEFORE Stasis()
asterisk -rx "dialplan show from-ai-agent"
```

### **Issue: Still hearing "star star"**
```bash
# Verify config was updated
docker exec ai-engine cat /app/config/contexts/demo-project-expert.yaml | grep "\\*\\*"
# Should return nothing

# If files not updated, rebuild:
docker-compose down
docker-compose build --no-cache ai-engine
docker-compose up -d
```

---

## 📞 **Support**

If issues persist:
1. Check full logs: `docker logs ai-engine > /tmp/ai-engine.log`
2. Review recent commits: `git log --oneline -10`
3. Compare with working main branch: `git diff main develop`

---

## ✅ **Deployment Complete!**

Your production server is now running with:
- ✅ Personalized caller name greetings
- ✅ Fixed TTS markdown reading issue
- ✅ All three pipelines validated and working

Test the demo line: **(925) 736-6718**
