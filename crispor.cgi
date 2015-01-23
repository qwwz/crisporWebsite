#!/usr/bin/env python
import subprocess, tempfile
import Cookie, time, math
import cgitb


# main script of the tefor crispr tool
# not broken into submodules to make installation easier

# cleaning things todo:
# - maybe: make temp subdirectories instead of many files with identical name

import sys, cgi, re, array, random, platform, os, hashlib, base64, string, logging, operator, urllib
from collections import defaultdict, namedtuple
from sys import stdout
from os.path import join, isfile, basename, dirname

DEBUG = False
#DEBUG = True

# the segments.bed files use abbreviated genomic region names
segTypeConv = {"ex":"exon", "in":"intron", "ig":"intergenic"}

# directory where processed batches of offtargets are stored ("cache" of bwa results)
batchDir = "temp"

DEFAULTORG = 'ensDanRer'
DEFAULTSEQ = 'CCAATCAGGTCCCTCCCTACCTCAGATCGCAGCTATAATACATAGGAGTAAAGAGGCTTCTCGCATTAAGTGGCTGTGGCTTGAAGTAACGTTGTGATTTCGAGGTCAGTCTTACCTTTCGCATCCCCGCCGCAAACCTCCGATGCGTTATCAGTCGCACGTTTCCGCACCTGTCACGGTCGGGGCTTGGCGCTGCTGAGGGACACGCGTGAACCGAGGAGACGGCAAGGACATCGCCGGAGATCCGCGCCTCGACAACGAGAAACCCTGCTAGACAGACCGCTCGAGAACACCGCAGCGAGATTCAGCGTGCGGCAAAATGCGGCTTTTGACGAGAGTGCTGCTGGTGTCTCTTCTCACTCTGTCCTTGGTGGTGTCCGGACTGGCCTGCGGTCCTGGCAGAGGCTACGGCAGAAGAAGACATCCGAAGAAGCTGACACCTCTCGCCTACAAGCAGTTCATACCTAATGTCGCGGAGAAGACCTTAGGGGCCAGCGGCAGATACGAGGGCAAGATAACGCGCAATTCGGAGAGATTTAAAGAACTTACTCCAAATTATAATCCCGACATTATCTTTAAGGATGAGGAGAACACGGGAGCGGACAGGCTCATGACACAG'

pamDesc = [ ('NGG','NGG - Streptococcus Pyogenes'),
         ('NNAGAA','NNAGAA - Streptococcus Thermophilus'),
         ('NNNNGMTT','NNNNG(A/C)TT - Neisseiria Meningitidis'),
         ('NNNNACA','NNNNACA - Campylobacter jejuni')
       ]

DEFAULTPAM = 'NGG'

# for some PAMs, we change the motif when searching for offtargets
# this is done by MIT and eCrisp
offtargetPams = {"NGG" : "NRG"}

def getParams():
    " get CGI parameters and return as dict "
    form = cgi.FieldStorage()
    params = {}

    for key in ["pamId", "batchId", "pam", "seq", "org", "showAll", "download"]:
        val = form.getfirst(key)
	if val!=None:
            params[key] = val

    if "pam" in params:
        if len(set(params["pam"])-set("ACTGNMK"))!=0:
            errAbort("Illegal character in PAM-sequence. Only ACTGMK and N allowed.")
    return params

def makeTempBase(seq, org, pam):
    "create the base of temp files using a hash function and some prettyfication "
    hasher = hashlib.sha1(seq+org+pam)
    batchId = base64.urlsafe_b64encode(hasher.digest()[0:20]).translate(transTab)[:20]
    return batchId

def saveSeqOrgPamToCookies(seq, org, pam):
    " create a cookie with seq, org and pam and print it"
    cookies=Cookie.SimpleCookie()
    expires = 365 * 24 * 60 * 60
    cookies['lastseq'] = seq
    cookies['lastseq']['expires'] = expires
    cookies['lastorg'] = org
    cookies['lastorg']['expires'] = expires
    cookies['lastpam'] = pam
    cookies['lastpam']['expires'] = expires
    print cookies

def debug(msg):
    if DEBUG:
        print msg
        print "<br>"

def errAbort(msg):
    print(msg+"<p>")
    sys.exit(0)

def matchNuc(pat, nuc):
    " returns true if pat (single char) matches nuc (single char) "
    if pat in ["A", "C", "T", "G"] and pat==nuc:
        return True
    elif pat=="M" and nuc in ["A", "C"]:
        return True
    elif pat=="K" and nuc in ["T", "G"]:
        return True
    else:
        return False

def findPat(seq, pat):
    """ yield positions where pat matches seq, stupid brute force search 
    """
    for i in range(0, len(seq)-len(pat)+1):
        #print "new pos", i, seq[i:i+len(pat)],"<br>"
        found = True
        for x in range(0, len(pat)):
            #print "new step", x, "<br>"
            if pat[x]=="N":
                #print "N","<br>"
                continue
            seqPos = i+x
            if seqPos == len(seq):
                found = False
                break
            if not matchNuc(pat[x], seq[seqPos]):
                #print i, x, pat[x], seq[seqPos], "no match<br>"
                found = False
                break
            #print "match", i, x, found, "<br>"
        if found:
            #print "yielding", i, "<br>"
            yield i

def rndSeq(seqLen):
    " return random seq "
    seq = []
    alf = "ACTG"
    for i in range(0, seqLen):
        seq.append(alf[random.randint(0,3)])
    return "".join(seq)

def cleanSeq(seq):
    """ remove fasta header, check seq for illegal chars and return (filtered seq, user message) 
    special value "random" returns a random sequence.
    """
    #print repr(seq)
    if seq.startswith("random"):
        seq = rndSeq(800)
    lines = seq.strip().splitlines()
    #print "<br>"
    #print "before fasta cleaning", "|".join(lines)
    if len(lines)>0 and lines[0].startswith(">"):
        line1 = lines.pop(0)
    #print "<br>"
    #print "after fasta cleaning", "|".join(lines)
    #print "<br>"

    newSeq = []
    nCount = 0
    for l in lines:
        if len(l)==0:
            continue
        for c in l:
            if c not in "actgACTG":
                nCount +=1 
            else:
                newSeq.append(c)
    seq = "".join(newSeq)

    msgs = []
    if len(seq)>2000:
        msgs.append("<strong>Sorry, this tool cannot handle sequences longer than 2kbp</strong><br>Below you find the results for the first 2000 bp of your input sequence.<br>")
        seq = seq[:2000]

    if nCount!=0:
        msgs.append("Sequence contained %d non-ACTG letters. They were removed." % nCount)

    return seq, "<br>".join(msgs)

def revComp(seq):
    " rev-comp a dna sequence with UIPAC characters "
    revTbl = {'A' : 'T', 'C' : 'G', 'G' : 'C', 'T' : 'A', 'N' : 'N' , 'M' : 'K', 'K':'M'}
    newSeq = []
    for c in reversed(seq):
        newSeq.append(revTbl[c])
    return "".join(newSeq)

def findPams(seq, pam, strand, startDict, endSet):
    " return two values: dict with pos -> strand of PAM and set of end positions of PAMs"

    minPos = 20
    maxPos = len(seq)-(20+len(pam))

    #print "new search", seq, pam, "<br>"
    for start in findPat(seq, pam):
        # need enough flanking seq on one side
        #print "found", start,"<br>"
        if strand=="+" and start<=minPos:
            #print "no, out of bounds +", "<br>"
            continue
        if strand=="-" and start>=maxPos:
            #print "no, out of bounds, -<br>"
            continue
            
        #print "match", strand, start, end, "<br>"
        startDict[start] = strand
        end = start+len(pam)
        endSet.add(end)
    return startDict, endSet

def rulerString(maxLen):
    " return line with positions every 10 chars "
    texts = []
    for i in range(0, maxLen, 10):
        numStr = str(i)
        texts.append(numStr)
        spacer = "".join([" "]*(10-len(numStr)))
        texts.append(spacer)
    return "".join(texts)

def showSeqAndPams(seq, lines, maxY, pam, guideScores):
    " show the sequence and the PAM sites underneath as a sequence viewer "
    print "<div class='substep'>"
    print '<a id="seqStart"></a>'
    print "There are %d possible guide sequences. Click on a PAM (%s) match to show its guide sequence.<br>" % (len(guideScores), pam)
    print "Shown below are the matches and the nucleotide 3' to corresponding cleavage site<br>"
    print '''Colors <span style="color:#32cd32; text-shadow: 1px 1px 1px #ddd">green</span>, <span style="color:#ffff00; text-shadow: 1px 1px 1px #ddd">yellow</span> and <span style="text-shadow: 1px 1px 1px #ddd; color:#aa0114">red</span> indicate high, medium and low specificity of the PAM's guide sequence in the genome.'''
    print "</div>"
    print '''<div style="text-align: left; overflow-x:scroll; width:100%; background:#DDDDDD; border-style: solid; border-width: 1px">'''

    print '<pre style="display:inline; line-height: 0.9em; text-align:left">'+rulerString(len(seq))
    print seq

    for y in range(0, maxY+1):
        #print "y", y, "<br>"
        texts = []
        lastEnd = 0
        for start, end, name, strand, pamId  in lines[y]:
            spacer = "".join([" "]*((start-lastEnd)))
            lastEnd = end
            texts.append(spacer)
            score = guideScores[pamId]
            color = scoreToColor(score)

            #print score, opacity
            texts.append('''<a style="text-shadow: 1px 1px 1px #bbb; color: %s" id="list%s" href="#%s" onmouseover="$('.hiddenExonMatch').show('fast');$('#show-more').hide();$('#show-less').show()" onfocus="window.location.href = '#seqStart'" >''' % (color, pamId,pamId))
            texts.append(name)
            texts.append("</a>")
        print "".join(texts)
    print("</pre><br>")

    print '''</div>'''
    
def flankSeqIter(seq, startDict, pamLen):
    """ given a seq and dictionary of pos -> strand and the length of the pamSite
    yield 20mers flanking the sites sorted by pos
    """
    startList = sorted(startDict.keys())
    for startPos in startList:
        strand = startDict[startPos]

        if strand=="+":
            flankSeq = seq[startPos-20:startPos]
            pamSeq = seq[startPos:startPos+pamLen]
        else: # strand is minus
            flankSeq = revComp(seq[startPos+pamLen:startPos+pamLen+20])
            pamSeq = revComp(seq[startPos:startPos+pamLen])

        yield startPos, strand, flankSeq, pamSeq

def printBrowserLink(dbInfo, pos, text, alnStr):
    " print link to genome browser (ucsc or ensembl) at pos, with given text "
    if dbInfo.server.startswith("Ensembl"):
        baseUrl = "www.ensembl.org"
        if dbInfo.server=="EnsemblPlants":
            baseUrl = "plants.ensembl.org"
        elif dbInfo.server=="EnsemblMetazoa":
            baseUrl = "metazoa.ensembl.org"
        org = dbInfo.scientificName.replace(" ", "_")
        url = "http://%s/%s/Location/View?r=%s" % (baseUrl, org, pos)
    elif dbInfo.server=="ucsc":
        if pos[0].isdigit():
            pos = "chr"+pos
        url = "http://genome.ucsc.edu/cgi-bin/hgTracks?db=%s&position=%s" % (dbInfo.name, pos)
    else:
        print "unknown genome browser server %s, please email penigault@tefor.net" % dbInfo.server

    print '''<a title="%s" target="_blank" href="%s">%s</a>''' % (alnStr, url, text)

def makeAlnStr(seq1, seq2, pam, score):
    " given two strings of equal length, return a html-formatted string that highlights the differences "
    lines = [ [], [], [] ]
    last12MmCount = 0
    for i in range(0, len(seq1)-len(pam)):
        if seq1[i]==seq2[i]:
            lines[0].append(seq1[i])
            lines[1].append(seq2[i])
            lines[2].append(" ")
        else:
            lines[0].append("<b>%s</b>"%seq1[i])
            lines[1].append("<b>%s</b>"%seq2[i])
            lines[2].append("*")
            if i>7:
                last12MmCount += 1
    #lines[0].append("<i>"+seq1[i:i+3]+"</i>")
    lines[0].append(" <i>"+seq1[-len(pam):]+"</i>")
    lines[1].append(" <i>"+seq2[-len(pam):]+"</i>")
    #lines[1].append("<i>"+seq2[i:i+3]+"</i>")
    lines = ["".join(l) for l in lines]
    htmlText = "<pre>guide:      %s<br>off-target: %s<br>            %s</pre>Off-target score: %.2f<br>" % (lines[0], lines[1], lines[2], score)
    hasLast12Mm = last12MmCount>0
    return htmlText, hasLast12Mm
        
#def calcMmScore(guideSeq, otSeq):
    #" return mismatch score for a given off target site "
    #guideSeq = guideSeq[:20]
    #otSeq = otSeq[:20]
    #score = 0
    #for i in range(len(guideSeq)-1, 0, -1):
        #if guideSeq[i]!=otSeq[i]:
            #score += i
    #return score

def parsePos(text):
    " parse a string of format chr:start-end:strand and return a 4-tuple "
    if len(text)!=0 and text!="?":
        chrom, posRange, strand = text.split(":")
        start, end = posRange.split("-")
        start, end = int(start), int(end)
    else:
        chrom, start, end, strand = "", 0, 0, "+"
    return chrom, start, end, strand

def makePosList(countDict, guideSeq, pam, inputPos):
    """ for a given guide sequence, return a list of (score, posStr, geneDesc,
    otSeq) sorted by score and a string to describe the offtargets in the
    format x/y/z/w of mismatch counts
    inputPos has format "chrom:start-end:strand". All 0MM matches in this range
    are ignored from scoring ("ontargets")
    Also return the same description for just the last 12 bp and the score 
    of the guide sequence (calculated using all offtargets).
    """
    inChrom, inStart, inEnd, inStrand = parsePos(inputPos)
    # one desc in last column per OT seq
    #countDict = otMatches[pamId]
    count = 0
    otCounts = []
    posList = []
    scores = []
    last12MmCounts = []

    # for each edit distance, get the off targets and iterate over them
    for editDist in range(0, 5):
        #print countDict,"<p>"
        matches = countDict.get(editDist, [])

        #print otCounts,"<p>"
        last12MmOtCount = 0

        # create html and score for every offtarget
        otCount = 0
        for chrom, start, end, otSeq, strand, segType, geneNameStr in matches:
            # skip on-targets
            if editDist==0 and chrom==inChrom and start >= inStart and end <= inEnd:
                continue
            otCount += 1
            posStr = "%s:%d-%s" % (chrom, int(start)+1,end)
            segTypeDesc = segTypeConv[segType]
            geneDesc = segTypeDesc+":"+geneNameStr
            geneDesc = geneDesc.replace("|", "-")
            score = calcHitScore(guideSeq[:20], otSeq[:20])
            scores.append(score)

            alnHtml, hasLast12Mm = makeAlnStr(guideSeq, otSeq, pam, score)
            if not hasLast12Mm:
                last12MmOtCount+=1
            posList.append( (score, editDist, posStr, geneDesc, alnHtml) )

        last12MmCounts.append(str(last12MmOtCount))
        # create a list of number of offtargets for this edit dist
        otCounts.append( str(otCount) )

    guideScore = calcMitGuideScore(sum(scores))

    posList.sort(reverse=True)
    otDescStr = "/".join(otCounts)
    last12DescStr = "/".join(last12MmCounts)

    return posList, otDescStr, guideScore, last12DescStr

# --- START OF SCORING ROUTINES 

# DOENCH SCORING 
params = [
# pasted/typed table from PDF and converted to zero-based positions
(1,'G',-0.2753771),(2,'A',-0.3238875),(2,'C',0.17212887),(3,'C',-0.1006662),
(4,'C',-0.2018029),(4,'G',0.24595663),(5,'A',0.03644004),(5,'C',0.09837684),
(6,'C',-0.7411813),(6,'G',-0.3932644),(11,'A',-0.466099),(14,'A',0.08537695),
(14,'C',-0.013814),(15,'A',0.27262051),(15,'C',-0.1190226),(15,'T',-0.2859442),
(16,'A',0.09745459),(16,'G',-0.1755462),(17,'C',-0.3457955),(17,'G',-0.6780964),
(18,'A',0.22508903),(18,'C',-0.5077941),(19,'G',-0.4173736),(19,'T',-0.054307),
(20,'G',0.37989937),(20,'T',-0.0907126),(21,'C',0.05782332),(21,'T',-0.5305673),
(22,'T',-0.8770074),(23,'C',-0.8762358),(23,'G',0.27891626),(23,'T',-0.4031022),
(24,'A',-0.0773007),(24,'C',0.28793562),(24,'T',-0.2216372),(27,'G',-0.6890167),
(27,'T',0.11787758),(28,'C',-0.1604453),(29,'G',0.38634258),(1,'GT',-0.6257787),
(4,'GC',0.30004332),(5,'AA',-0.8348362),(5,'TA',0.76062777),(6,'GG',-0.4908167),
(11,'GG',-1.5169074),(11,'TA',0.7092612),(11,'TC',0.49629861),(11,'TT',-0.5868739),
(12,'GG',-0.3345637),(13,'GA',0.76384993),(13,'GC',-0.5370252),(16,'TG',-0.7981461),
(18,'GG',-0.6668087),(18,'TC',0.35318325),(19,'CC',0.74807209),(19,'TG',-0.3672668),
(20,'AC',0.56820913),(20,'CG',0.32907207),(20,'GA',-0.8364568),(20,'GG',-0.7822076),
(21,'TC',-1.029693),(22,'CG',0.85619782),(22,'CT',-0.4632077),(23,'AA',-0.5794924),
(23,'AG',0.64907554),(24,'AG',-0.0773007),(24,'CG',0.28793562),(24,'TG',-0.2216372),
(26,'GT',0.11787758),(28,'GG',-0.69774)]

intercept =  0.59763615
gcHigh    = -0.1665878
gcLow     = -0.2026259

def calcDoenchScore(seq):
    assert(len(seq)==30)
    score = intercept

    guideSeq = seq[4:24]
    gcCount = guideSeq.count("G") + guideSeq.count("C")
    if gcCount <= 10:
        gcWeight = gcLow
    if gcCount > 10:
        gcWeight = gcHigh
    score += abs(10-gcCount)*gcWeight

    for pos, modelSeq, weight in params:
        subSeq = seq[pos:pos+len(modelSeq)]
        if subSeq==modelSeq:
            score += weight
    return 1.0/(1.0+math.exp(-score))

# MIT offtarget scoring

def calcHitScore(string1,string2):
    " see 'Scores of single hits' on http://crispr.mit.edu/about "
    # The Patrick Hsu weighting scheme
    M = [0,0,0.014,0,0,0.395,0.317,0,0.389,0.079,0.445,0.508,0.613,0.851,0.732,0.828,0.615,0.804,0.685,0.583]
    assert(len(string1)==len(string2)==20)

    dists = [] # distances between mismatches, for part 2
    mmCount = 0 # number of mismatches, for part 3
    lastMmPos = None # position of last mismatch, used to calculate distance

    score1 = 1.0
    for pos in range(0, len(string1)):
        if string1[pos]!=string2[pos]:
            mmCount+=1
            if lastMmPos!=None:
                dists.append(pos-lastMmPos)
            score1 *= 1-M[pos]
            lastMmPos = pos
    # 2nd part of the score
    if mmCount<2: # special case, not shown in the paper
        score2 = 1.0
    else:
        avgDist = sum(dists)/len(dists)
        score2 = 1.0 / (((19-avgDist)/19.0) * 4 + 1)
    # 3rd part of the score
    if mmCount==0: # special case, not shown in the paper
        score3 = 1.0
    else:
        score3 = 1.0 / (mmCount**2)

    score = score1 * score2 * score3 * 100
    return score

def calcMitGuideScore(hitSum):
    " Sguide defined on http://crispr.mit.edu/about "
    score = 100 / (100+hitSum)
    score = int(round(score*100))
    return score

# --- END OF SCORING ROUTINES 

def calcDoenchScoreFromSeqPos(startPos, seq, pamLen, strand):
    """ extract 30 mer from seq given beginning of pam at startPos and strand,
    return Doench score 
    """
    if strand=="+":
        fromPos  = startPos-24
        toPos    = startPos+6
        func     = str
    else: # strand is minus
        fromPos = startPos+pamLen-6
        toPos   = startPos+pamLen+24
        func    = revComp

    if fromPos < 0 or toPos > len(seq):
        return 0
    else:
        seq30Mer = func(seq[fromPos:toPos]) 
        return int(round(100*calcDoenchScore(seq30Mer)))

def htmlHelp(text):
    " show help text with tooltip or modal dialog "
    print '''<img src="image/info.png" class="help tooltip" title="%s" />''' % text

def readEnzymes():
    " parse restrSites.txt and return as dict length -> list of (name, seq) "
    fname = "restrSites.txt"
    enzList = {}
    for line in open(fname):
        name, seq1, seq2 = line.split()
        seq = seq1+seq2
        enzList.setdefault(len(seq), []).append( (name, seq) )
    return enzList
        
def patMatch(seq, pat, notDegPos=None):
    """ return true if pat matches seq, both have to be same length 
    do not match degenerate codes at position notDegPos (0-based)
    """
    assert(len(seq)==len(pat))
    for x in range(0, len(pat)):
        patChar = pat[x]
        nuc = seq[x]

        assert(patChar in "MKYRACTGN")
        assert(nuc in "MKYRACTGN")

        if notDegPos!=None and x==notDegPos and patChar!=nuc:
            #print x, seq, pat, notDegPos, patChar, nuc, "<br>"
            return False

        if patChar=="N":
            continue
        if patChar=="M" and nuc in ["A", "C"]:
            continue
        if patChar=="K" and nuc in ["T", "G"]:
            continue
        if patChar=="R" and nuc in ["A", "G"]:
            continue
        if patChar=="Y" and nuc in ["C", "T"]:
            continue
        if patChar!=nuc:
            return False
    return True

def findSite(seq, restrSite):
    """ return the positions where restrSite matches seq 
    seq can be longer than restrSite
    Do not allow degenerate characters to match at position len(restrSite) in seq
    """
    posList = []
    for i in range(0, len(seq)-len(restrSite)+1):
        subseq = seq[i:i+len(restrSite)]
        #print subseq==restrSite, subseq, restrSite,"<br>"

        # JP does not want any potential site to be suppressed
        #if i<len(restrSite):
            #isMatch = patMatch(subseq, restrSite, len(restrSite)-i-1)
        #else:
            #isMatch = patMatch(subseq, restrSite)
        isMatch = patMatch(subseq, restrSite)

        if isMatch:
            posList.append( (i, i+len(restrSite)) )
    return posList

def matchRestrEnz(allEnzymes, guideSeq, pamSeq):
    """ return list of enzymes that overlap the -3 position in guideSeq 
    returns dict name -> list of matching positions
    """
    matches = {}
    for siteLen, sites in allEnzymes.iteritems():
        # sequence of +/-siteLen around the position -3 in guideSeq
        # the sequence goes into the PAM site
        startSeq = -3-siteLen+1
        seq = guideSeq[startSeq:]+pamSeq[:siteLen-3]
        for name, restrSite in sites:
            posList = findSite(seq, restrSite)
            if len(posList)!=0:
                liftOffset = startSeq+len(guideSeq)
                posList = [(liftOffset+x, liftOffset+y) for x,y in posList]
                matches.setdefault(name, []).extend(posList)
            #print name, seq, restrSite, findSite(seq, restrSite), "<br>"
    return matches

def scoreGuides(seq, startDict, pamPat, otMatches, inputPos):
    " for each pam in startDict, retrieve the guide sequence next to it and score it "
    allEnzymes = readEnzymes()

    guideData = []
    guideScores = {}
    hasNotFound = False

    for startPos, strand, guideSeq, pamSeq in flankSeqIter(seq, startDict, len(pamPat)):
        # position with anchor to jump to
        pamId = "s"+str(startPos)+strand
        # flank seq
        #seqStr = "<tt>"+flankSeq + " <i>" + pamSeq+"</i></tt>"

        # matches in genome
        # one desc in last column per OT seq
        if pamId in otMatches:
            guideSeqFull = guideSeq + pamSeq
            mutEnzymes = matchRestrEnz(allEnzymes, guideSeq, pamSeq)
            posList, otDesc, guideScore, last12Desc = makePosList(otMatches[pamId], guideSeqFull, pamPat, inputPos)
            effScore = calcDoenchScoreFromSeqPos(startPos, seq, len(pamPat), strand)
        else:
            posList, otDesc, guideScore = None, "No match. Incorrect genome or cDNA?", 0
            last12Desc = ""
            effScore = 0
            hasNotFound = True
            mutEnzymes = []
        guideData.append( (guideScore, effScore, startPos, strand, pamId, guideSeq, pamSeq, posList, otDesc, last12Desc, mutEnzymes) )
        guideScores[pamId] = guideScore

    guideData.sort(reverse=True)
    #guideData.sort(reverse=True, key=lambda row: 3*row[0]+row[1])
    return guideData, guideScores, hasNotFound

def printTableHead():
    " print guide score table description and columns "
    # one row per guide sequence
    print '''<div class='substep'>Ranked from highest to lowest specificity score determined as in <a target='_blank' href='http://dx.doi.org/10.1038/nbt.2647'>Hsu et al.</a> and on <a href="http://crispr.mit.org">http://crispr.mit.org</a>. <br>Also provided are efficacy scores, see <a href="http://www.nature.com/nbt/journal/v32/n12/full/nbt.3026.html">Doench et al.</a> and GC content, see <a href="http://www.cell.com/cell-reports/abstract/S2211-1247%2814%2900827-4">Ren et al.</a><br></div>'''
    print '<table id="otTable">'
    print '<tr style="border-left:5px solid black">'
    
    print '<th>Position/<br>Strand'
    print '</th>'

    print '<th style="width:170px">Guide Sequence + <i>PAM</i><br>Restriction Enzymes'
    htmlHelp("Restriction enzymes potentially useful for screening mutations induced by the guide RNA.<br> These enzyme sites overlap the position 3bp 5' to the PAM <br> and will usually be inactivated if the DNA was cut by Cas9.")

    print '<th>Specificity Score'
    htmlHelp("The specificity score measure the uniqueness of a guide in the genome. &lt;br&gt;The higher the specificity score, the less likely are off-target effects. See Hsu et al.")
    print "</th>"

    print '<th>Efficacy Score'
    htmlHelp("The efficacy score predicts the cutting efficiency of the nuclease on a sequence. &lt;br&gt; The higher the efficacy score, the more likely is cutting. For details see Doench et al.")
    print "</th>"

    print "<th>high G/C content next to PAM"
    htmlHelp("Ren, Zhihao, Jiang et al (Cell Reports 2014) showed that GC content in the 6 bp <br>adjacent to the PAM site is correlated with activity (P=0.625). <br>When >=4, the guide RNA tested in Drosophila usually induced a heritable mutation rate over 60%.")
    print '</th>'

    print '<th>Off-targets for 0,1,2,3,4 mismatches</th>'

    print '<th>Off-targets with no mismatches adjacent to PAM</i>'
    htmlHelp("Off-targets with all mismatches located in the 8 bp at the five-prime end of the guide RNA sequences.<br> Because these potential off-targets have no mismatches in the 12 bp <br>adjacent to the PAM, they are thought to have a higher probability of being cut.")
    print "</th>"

    print '<th>Genome Browser links to matches sorted by off-target score'
    htmlHelp("For each off-target the number of mismatches is indicated and linked to a genome browser. <br>Matches are ranked by off-target score (see Hsu et al) from most to least likely.")
    print "</th>"

def scoreToColor(guideScore):
    if guideScore > 50:
        color = "#32cd32"
    elif guideScore > 20:
        color = "#ffff00"
    else:
        color = "#aa0114"
    return color

def showGuideTable(guideData, pam, otMatches, dbInfo, batchId, org, showAll):
    " shows table of all PAM motif matches "
    print "<br><div class='title'>Predicted guide sequences for PAMs</div>" 
    printTableHead()

    count = 0
    for guideRow in guideData:
        guideScore, effScore, startPos, strand, pamId, \
            guideSeq, pamSeq, posList, otDesc, last12Desc, mutEnzymes = guideRow

        color = scoreToColor(guideScore)
        print '<tr id="%s" style="border-left: 5px solid %s">' % (pamId, color)

        # position and strand
        #print '<td id="%s">' % pamId
        print '<td>'
        print '<a href="#list%s">' % (pamId)
        print str(startPos)+" /"
        if strand=="+":
            print 'fw'
        else:
            print 'rev'
        print '</a>'
        print "</td>"

        # sequence
        print "<td>"
        print "<small>"
        print "<tt>"+guideSeq + " <i>" + pamSeq+"</i></tt>"
        print "<br>"

        if len(mutEnzymes)!=0:
            print "Restr. Enzymes:"
            print ",".join(mutEnzymes)
        print "<br>"

        scriptName = basename(__file__)
        if posList!=None:
            print '<a href="%s?batchId=%s&pamId=%s&pam=%s">Primers</a>' % (scriptName, batchId, urllib.quote(str(pamId)), pam)
        print "</small>"
        print "</td>"

        # off-target score, aka specificity score aka MIT score
        print "<td>"
        print "%d" % guideScore
        print "</td>"

        # efficacy score
        print "<td>"
        if effScore==None:
            print "Too close to end"
        else:
            print '''%d''' % effScore
        #print '''<a href="#" onclick="alert('%s')">%0.2f</a>''' % (effScore)
        #print "<!-- %s -->" % seq30Mer
        print "</td>"

        # close GC > 4
        print "<td>"
        gcCount = guideSeq[-6:].count("G")+guideSeq[-6:].count("C")
        if gcCount >= 4:
            print "+"
        else:
            print "-"
        print "</td>"

        # mismatch description
        print "<td>"
        print otDesc
        #otCount = sum([int(x) for x in otDesc.split("/")])
        otCount = len(posList)
        print "<br><small>%d off-targets</small>" % otCount
        if posList==None:
            # no genome match
            htmlHelp("Sequence was not found in genome.<br>If you have pasted a cDNA sequence, note that sequences that overlap a splice site cannot be used as guide sequences<br>This warning also occurs if you have selected the wrong genome.")
        print "</td>"

        # mismatch description, last 12 bp
        print "<td>"
        print last12Desc
        print "</td>"

        # links to offtargets
        print "<td><small>"
        if posList!=None:
            i = 0
            for score, editDist, pos, gene, alnHtml in posList:
                print '''%d:''' % (int(editDist))
                printBrowserLink(dbInfo, pos, gene, alnHtml)
                i+=1
                if i==3 and not showAll:
                    break
            if not showAll and len(posList)>3:
                 print '''... <br>&nbsp;&nbsp;&nbsp;<a href="crispor.cgi?batchId=%s&showAll=1">- show all offtargets</a>''' % batchId

        print "</small></td>"

        print "</tr>"
        count = count+1

    print "</table>"

    #print '''<a style="text-align:right" href="http://tefor.net/crispor/download.php?batchId=%s&amp;seq=%s&amp;org=%s&amp;pam=%s&amp;pamId=%s">
                    #<img style="width:20px;vertical-align:middle"
                         #src="http://tefor.net/crispor/image/doc.png">
                    #Download results
                #<!--</div>-->
            #</a>
            #<br><br>
    #''' % (batchId,seq,org,pam,pamId)


def printHeader(batchId):
    " print the html header "

    print "<html><head>"   

    runPhp("header.php")
    runPhp("/var/www/main/specific/googleanalytics/script.php")

    # activate jqueryUI tooltips
    print ("""  <script>
               $(function () {
                  $(document).tooltip({
                  relative : true,
                  content: function () {
                  return $(this).prop('title');
                  }
                 });
              });
              </script>""")

    print '<link rel="stylesheet" type="text/css" href="style/tooltipster.css" />'
    print '<link rel="stylesheet" type="text/css" href="style/tooltipster-shadow.css" />'

    # the UFD combobox, https://code.google.com/p/ufd/wiki/Usage
    print '<script type="text/javascript" src="js/jquery.ui.ufd.min.js"></script>'
    print '<link rel="stylesheet" type="text/css" href="style/ufd-base.css" />'
    print '<link rel="stylesheet" type="text/css" href="style/plain.css" />'
    print '<link rel="stylesheet" type="text/css"  href="http://code.jquery.com/ui/1.11.1/themes/smoothness/jquery-ui.css" />'
    print '<script type="text/javascript" src="js/jquery.tooltipster.min.js"></script>'

    # activate tooltipster
   #theme: 'tooltipster-shadow',
    print (""" <script> $(document).ready(function() { $('.tooltip').tooltipster({ 
        contentAsHTML: true,
       speed : 0
        }); }); </script> """)

    # activate Jquery UI tooltips
    print("""<style>
        .ui-tooltip {
            background-color: #FFFFFF;
            width: 400;
            height: 110;
            position : absolute;
            text-align: left;
            border:1px solid #cccccc;
            }
            </style>""")
    print("</head>")

    print'<body id="wrapper"'
    
    if batchId is not None:
        print '''
        onload="history.pushState('crispor/crisporDev.cgi', document.title, '?batchId=%s');"
        ''' % (batchId)
    print'>'
    print "<div id='fb-root'></div>"
    print('<script src="http://tefor.net/crispor/facebooklike.js"></script>')    

def firstFreeLine(lineMasks, y, start, end):
    " recursively search for first free line to place a feature (start, end) "
    #print "called with y", y
    if y>=len(lineMasks):
        return None
    lineMask = lineMasks[y]
    for x in range(start, end):
        if lineMask[x]!=0:
            return firstFreeLine(lineMasks, y+1, start, end)
        else:
            return y
    return None

def distrOnLines(seq, startDict, featLen):
    """ given a dict with start -> (start,end,name,strand) and a motif len, create lines of annotations such that
        the motifs don't overlap on the lines 
    """
    # max number of lines in y direction to draw
    MAXLINES = 18
    # amount of free space around each feature
    SLOP = 2

    # bitmask, one per line, 1 = we have a feature here, 0 = no feature here
    lineMasks = []
    for i in range(0, MAXLINES):
        lineMasks.append( [0]* (len(seq)+10) )

    # dict with lineCount (0...MAXLINES) -> list of (start, strand) tuples
    ftsByLine = defaultdict(list)
    maxY = 0
    for start in sorted(startDict):
        end = start+featLen
        strand = startDict[start]

        ftSeq = seq[start:end] 
        if strand=="+":
            label = '%s..%s'%(seq[start-3].lower(), ftSeq)
            startFt = start - 3
            endFt = end
        else:
            label = '%s..%s'%(ftSeq, seq[end+2].lower())
            startFt = start
            endFt = end + 3

        y = firstFreeLine(lineMasks, 0, startFt, endFt)
        if y==None:
            errAbort("not enough space to plot features")

        # fill the current mask
        mask = lineMasks[y]
        for i in range(max(startFt-SLOP, 0), min(endFt+SLOP, len(seq))):
            mask[i]=1

        maxY = max(y, maxY)

        pamId = "s%d%s" % (start, strand)
        ft = (startFt, endFt, label, strand, pamId) 
        ftsByLine[y].append(ft )
    return ftsByLine, maxY

def writePamFlank(seq, startDict, pam, faFname):
    " write pam flanking sequences to fasta file "
    #print "writing pams to %s<br>" % faFname
    faFh = open(faFname, "w")
    for startPos, strand, flankSeq, pamSeq in flankSeqIter(seq, startDict, len(pam)):
        faFh.write(">s%d%s\n%s\n" % (startPos, strand, flankSeq))
    faFh.close()

def runCmd(cmd):
    " run shell command, check ret code, replaces BIN and SCRIPTS special variables "
    sysId = platform.system()
    binDir = "bin/"+sysId
    scriptDir = "bin"
    if __file__.endswith("Dev.cgi"):
        scriptDir = "binDev"

    cmd = cmd.replace("BIN", binDir)
    cmd = cmd.replace("SCRIPT", scriptDir)
    cmd = "set -o pipefail; " + cmd
    debug("Running %s" % cmd)
    #print cmd
    #ret = os.system(cmd)
    ret = subprocess.call(cmd, shell=True, executable="/bin/bash")
    if ret!=0:
        print "Server error: could not run command %s.<p>" % cmd
        print "please send us an email, we will fix this error as quickly as possible. penigault@tefor.net "
        sys.exit(0)

def parseOfftargets(bedFname):
    """ parse a bed file with annotataed off target matches from overlapSelect,
    has two name fields, one with the pam position/strand and one with the
    overlapped segment 
    
    return as dict pamId -> editDist -> (chrom, start, end, seq, strand, segType, segName)
    segType is "ex" "int" or "ig" (=intergenic)
    if intergenic, geneNameStr is two genes, split by |
    """
    # example input:
    # chrIV 9864393 9864410 s41-|-|5    chrIV   9864303 9864408 ex:K07F5.16
    # chrIV   9864393 9864410 s41-|-|5    chrIV   9864408 9864470 in:K07F5.16
    debug("reading %s" % bedFname)

    # if a offtarget overlaps an intron/exon or ig/exon boundary it will appear twice
    # in this case, we only keep the exon offtarget
    # first sort into dict (pamId,chrom,start,end,editDist,strand) -> (segType, segName)
    #pamData = defaultdict(dict)
    pamData = {}
    for line in open(bedFname):
        fields = line.rstrip("\n").split("\t")
        chrom, start, end, name, segment = fields
        pamId, strand, editDist, seq = name.split("|")
        editDist = int(editDist)
        # some gene models include colons
        segType, segName = string.split(segment, ":", maxsplit=1)
        #pamData[(pamId].setdefault(editDist, []).append( (chrom, start, end, strand, segType, segName) )
        start, end = int(start), int(end)
        otKey = (pamId, chrom, start, end, editDist, seq, strand)
        if otKey in pamData and segType!="ex":
            continue
        pamData[otKey] = (segType, segName)

    indexedOts = defaultdict(dict)
    for otKey, otVal in pamData.iteritems():
        pamId, chrom, start, end, editDist, seq, strand = otKey
        segType, segName = otVal
        indexedOts[pamId].setdefault(editDist, []).append( (chrom, start, end, seq, strand, segType, segName) )

    return indexedOts

def findOfftargets(faFname, genome, pam, bedFname):
    " search fasta file against genome, filter for pam matches and write to bedFName "
    pamLen = len(pam)
    # potentially use a PAM for OTs that is different from the guide PAM
    pam = offtargetPams.get(pam, pam)

    cmd = "BIN/bwa aln -n 4 -o 0 -k 4 -N -l 20 -m 1000000000 genomes/%(genome)s/%(genome)s.fa %(faFname)s | BIN/bwa samse -n 100000000000 genomes/%(genome)s/%(genome)s.fa /dev/stdin %(faFname)s  | SCRIPT/xa2multi.pl | SCRIPT/samToBed %(pamLen)s | BIN/bedClip stdin genomes/%(genome)s/%(genome)s.sizes stdout | BIN/twoBitToFa genomes/%(genome)s/%(genome)s.2bit stdout -bed=stdin | SCRIPT/filterFaToBed %(pam)s | BIN/overlapSelect genomes/%(genome)s/%(genome)s.segments.bed stdin stdout -mergeOutput -selectFmt=bed -inFmt=bed | cut -f1,2,3,4,8 2> /tmp/log > %(bedFname)s " % locals()
    #cmd = "echo mainScript > /tmp/log"
    runCmd(cmd)

transTab = string.maketrans("-=/+_", "abcde")

def lineFileNext(fh):
    """ 
        parses tab-sep file with headers as field names 
        yields collection.namedtuples
        strips "#"-prefix from header line
    """
    line1 = fh.readline()
    line1 = line1.strip("\n").strip("#")
    headers = line1.split("\t")
    Record = namedtuple('tsvRec', headers)
   
    for line in fh:
        line = line.rstrip("\n")
        fields = line.split("\t")
        try:
            rec = Record(*fields)
        except Exception, msg:
            logging.error("Exception occured while parsing line, %s" % msg)
            logging.error("Filename %s" % fh.name)
            logging.error("Line was: %s" % repr(line))
            logging.error("Does number of fields match headers?")
            logging.error("Headers are: %s" % headers)
            #raise Exception("wrong field count in line %s" % line)
            continue
        # convert fields to correct data type
        yield rec

def readGenomes():
    " return list of genomes supported "
    genomes = {}

    myDir = dirname(__file__)
    genomesDir = join(myDir, "genomes")
    for subDir in os.listdir(genomesDir):
        infoFname = join(genomesDir, subDir, "genomeInfo.tab")
        if isfile(infoFname):
            row = lineFileNext(open(infoFname)).next()
            # add a note to identify UCSC genomes
            if row.server.startswith("ucsc"):
                addStr="UCSC "
            else:
                addStr = ""
            genomes[row.name] = row.scientificName+" - "+row.genome+" - "+addStr+row.description
            #genomes[row.name] = row.genome+" - "+row.scientificName+" - "+row.description

    genomes = genomes.items() 
    genomes.sort(key=operator.itemgetter(1))
    return genomes

def printOrgDropDown(lastorg):
    " print the organism drop down box "
    genomes = readGenomes()
    print '<select id="genomeDropDown" class style="width:350; max-width:400px; float:left" name="org" tabindex="2">'
    for db, desc in genomes:
        print '<option '
        if db == lastorg :
            print 'selected '
        print 'value="%s">%s</option>' % (db, desc)
    print "</select>"
    print ('''
      <script type="text/javascript">
      $("#genomeDropDown").ufd();
      </script>''')
    print ('''<br>''')

def printPamDropDown(lastpam):        
    
    print '<select style="float:left" name="pam" tabindex="3">'
    for key,value in pamDesc:        
        print '<option '
        if key == lastpam :
            print 'selected '
        print 'value="%s">%s</option>' % (key, value)
    print "</select>"           

def printForm(params):
    " print html input form "
    #seq, org, pam = params["seq"], params["org"], params["pam"]
    scriptName = basename(__file__)

    # The returned cookie is available in the os.environ dictionary
    cookies=Cookie.SimpleCookie(os.environ.get('HTTP_COOKIE'))
    if "lastorg" in cookies and "lastseq" in cookies and "lastpam" in cookies:
       lastorg   = cookies['lastorg'].value
       lastseq   = cookies['lastseq'].value
       lastpam   = cookies['lastpam'].value
    else:
       lastorg = DEFAULTORG
       lastseq = DEFAULTSEQ
       lastpam = DEFAULTPAM

    print """
<form id="main-form" method="post" action="%s">

<div class="introtext">
 CRISPOR (CRISPr selectOR) is a program that helps design and evaluate target sites for use with the CRISPR/Cas9 system.
    <div onclick="$('#about-us').toggle('fast');" class="title" style="cursor:pointer;display:inline;font-size:large;font-style:normal">
        <img src="http://tefor.net/crispor/image/info.png" class="infopoint" style="vertical-align:text-top;">
    </div>
    <div id="about-us"><br>
    CRISPOR uses the BWA algorithm to identify guide RNA sequences for CRISPR mediated genome editing.<br>
    It searches for off-target sites (with and without mismatches), shows them in a table and annotates them with flanking genes.<br>
    For more information on principles of CRISPR-mediated genome editing, check the <a href="https://www.addgene.org/CRISPR/guide/">Addgene CRISPR guide</a>.</div>
</div>

<div class="windowstep subpanel" style="width:50%%;">
    <div class="substep">
        <div class="title" style="cursor:pointer;" onclick="$('#helptext1').toggle('fast')">
            Step 1
            <img src="http://tefor.net/crispor/image/info.png" class="infopoint" >
        </div>
       Submit a single sequence for guide RNA identification and analysis
    </div>

    <textarea tabindex="1" style="width:100%%;" name="seq" rows="10"
              placeholder="Enter the sequence of the gene you want to target - example: %s">
    %s
    </textarea>
    <div id="helptext1" class="helptext">CRISPOR conserves the lowercase and uppercase format of your sequence (allowing to highlight sequence features of interest such as ATG or STOP codons)</div>

    <input style="margin-top:20px;" type="submit" name="submit" value="SUBMIT" tabindex="4"/>
</div>
<div class="windowstep subpanel" style="width:40%%">
    <div class="substep">
        <div class="title" style="cursor:pointer;" onclick="$('#helpstep2').toggle('fast')">
            Step 2
            <img src="http://tefor.net/crispor/image/info.png" class="infopoint">
        </div>
        Choose a species genome

    </div>
    """% (scriptName,lastseq,lastseq)

    printOrgDropDown(lastorg)
    print '<small style="float:left">Type a species name to search for it</small>'

    print """<div id="helpstep2" class="helptext">More information on these species can be found on the <a href="http://www.efor.fr">EFOR</a> website.
To add your genome of interest to the list, contact CRISPOR web site manager
<a href="mailto:penigault@tefor.net">Jean-Baptiste Penigault</a>.</div>
"""
    print """
</div>
<div class="windowstep subpanel" style="width:40%%">
    <div class="substep">
        <div class="title" style="cursor:pointer;" onclick="$('#helpstep3').toggle('fast')">
            Step 3
            <img src="http://tefor.net/crispor/image/info.png" class="infopoint">
        </div>
        Choose a Protospacer Adjacent Motif (PAM)
    </div>
    """
    printPamDropDown(lastpam)
    print """
    <div id="helpstep3" class="helptext">The most common system uses the NGG PAM recognized by Cas9 from S. <i>pyogenes</i></div>
</div>
</form>
    """

def batchParams(batchId):
    """ given a batchId, return the genome, the pam, the input sequence and the
    chrom pos. Returns None for pos if not found. """

    batchBase = join(batchDir, batchId)
    faFname = batchBase+".input.fa"
    ifh = open(faFname)
    ifhFields = ifh.readline().replace(">","").strip().split()
    if len(ifhFields)==2:
        genome, pamSeq = ifhFields
        position = None
    else:
        genome, pamSeq, position = ifhFields
    inSeq = ifh.readline().strip()
    ifh.close()
    return inSeq, genome, pamSeq, position

def crisprSearch(params):
    " do crispr off target search "
    if "batchId" in params:
        # if we're getting only the batchId, extract the parameters from the batch
        # this allows a stable link to a batch that is done
        seq, org, pam, position = batchParams(params["batchId"])
        batchId = params["batchId"]
        batchBase = join(batchDir, batchId)
        # older batch files don't include the position
        if position==None:
            position = findBestMatch(org, seq)
    else:
        seq, org, pam = params["seq"], params["org"], params["pam"]
        batchId = makeTempBase(seq, org, pam)

        position = findBestMatch(org, seq)

        # define temp file names
        batchBase = join(batchDir, batchId)
        #print "<!-- BATCH ID %s -->" % batchId

        # save input seq, pamSeq, genome, position for primer design later
        inputFaFname = batchBase+".input.fa"
        open(inputFaFname, "w").write(">%s %s %s\n%s\n" % (org, pam, position, seq))

    showAll = (params.get("showAll", 0)=="1")

    # read genome info tab file
    myDir = dirname(__file__)
    genomesDir = join(myDir, "genomes")
    infoFname = join(genomesDir, org, "genomeInfo.tab")
    dbInfo = lineFileNext(open(infoFname)).next()

    caseSeq, userMsg = cleanSeq(seq)
    seq = caseSeq.upper()
    if len(userMsg)!=0:
        print userMsg

    # search pams
    startDict, endSet = findPams(seq, pam, "+", {}, set())
    startDict, endSet = findPams(seq, revComp(pam), "-", startDict, endSet)

    # write guides to fasta and run bwa
    faFname = batchBase+".fa"
    otBedFname = batchBase+".bed"
    flagFile = batchBase+".running"

    if isfile(flagFile):
       errAbort("This sequence is still being processed. Please wait for ~20 seconds "
           "and try again, e.g. by reloading this page. If you see this message for "
           "more than 1-2 minutes, please email penigault@tefor.net")

    if not isfile(otBedFname):
        # write potential PAM sites to file
        writePamFlank(seq, startDict, pam, faFname)
        findOfftargets(faFname, org, pam, otBedFname)

    if position!='?':
        genomePosStr = ":".join(position.split(":")[:2])
        print "<div class='title'><em>%s</em> sequence at " % (dbInfo.scientificName)
        printBrowserLink(dbInfo, genomePosStr, genomePosStr, "")
        print "</div>"
    else:
        print "<div class='title'>Query sequence, not present in the genome of %s</div>" % dbInfo.scientificName
        print "<div class='substep'>"
        print "<em>Note: The query sequence was not found in the selected genome."
        print "This can be a valid query, e.g. a GFP sequence.<br>"
        print "If not, you might want to check if you selected the right genome for your query sequence.<br>"
        print "When reading the list of guide sequences and off-targets below, bear in mind that the software cannot distinguish off-targets from on-targets now, so some 0-mismatch targets are expected. In this case, the scores of guide sequences are too low.<p>"
        print "</em></div>"

    otMatches = parseOfftargets(otBedFname)


    featLen = len(pam)
    lines, maxY = distrOnLines(seq, startDict, featLen)

    guideData, guideScores, hasNotFound = scoreGuides(seq, startDict, pam, otMatches, position)

    if hasNotFound:
        print("<strong>Warning:</strong> At least one of the PAM-flanking sequences was not found in the genome.<br>")
        print("Did you select the right genome? <br>")
        print("If you pasted a cDNA sequence, note that sequences with score 0 are not in the genome, only in the cDNA and are not usable as CRISPR guides.<br>")

    showSeqAndPams(caseSeq, lines, maxY, pam, guideScores)

    showGuideTable(guideData, pam, otMatches, dbInfo, batchId, org, showAll)

    # XX are back buttons necessary in 2014?
    print '<a style="float:right" href="crispor.cgi?batchId=%s&download=offtargets">Download off-targets</a>' % batchId
    print '<br><a class="neutral" href="crispor.cgi">'
    print '<div class="button" style="margin-left:auto;margin-right:auto;width:90;">New Query</div></a>'

def runPhp(script):
    " run a file through php, write result to stdout. accepts a full or a relative path "
    if "/" in script:
        path = script
    else:
        myDir = dirname(__file__)
        path = "%s/%s" % (myDir, script)

    proc = subprocess.Popen("php "+path, shell=True, stdout=subprocess.PIPE)
    script_response = proc.stdout.read()
    print script_response

def printTeforBodyStart():
    print "<div class='logo'><a href='http://tefor.net/main/'><img src='http://tefor.net/main/images/logo/logo_tefor.png' alt='logo tefor infrastructure'></a></div>"
    runPhp("menu.php")

    print '<div id="bd">'
    print '<div class="centralpanel">'
    runPhp("networking.php")
    print '<div class="subpanel" style="background:transparent;box-shadow:none;">'
    print '<div class="contentcentral" style="background-color:transparent;">'

def printTeforBodyEnd():
    print '</div>'
    print '</div>'
    print '</div>'
    runPhp("footer.php")
    print '</div>'

def downloadFile(params):
    " "
    #print "Content-type: application/octet-stream\n"
    print "Content-Disposition: attachment; filename=\"offtargets.xls\""
    print "" # = end of http headers
    if params["download"]=="offtargets":
        fname = join("temp", params["batchId"]+".bed")
        for line in open(fname):
            print line,

def printBody(params):
    " main dispatcher function "
    if len(params)==0:
        printForm(params)
    elif "batchId" in params and "pamId" in params and "pam" in params:
        primerDetailsPage(params)
    elif ("seq" in params and "org" in params and "pam" in params) \
                or "batchId" in params:
        crisprSearch(params)
    else:
        errAbort("Unrecognized CGI parameters.")

def parseBoulder(tmpOutFname):
    " parse a boulder IO style file, as output by Primer3 "
    data = {}
    for line in open(tmpOutFname):
        key, val = line.rstrip("\n").split("=")
        data[key] = val
    return data

def runPrimer3(seq, tmpInFname, tmpOutFname, targetStart, targetLen, prodSizeRange):
        """ return primers from primer3 in format seq1, tm1, pos1, seq2, tm2, pos2"""
        conf = """SEQUENCE_TEMPLATE=%(seq)s
PRIMER_TASK=generic
PRIMER_PICK_LEFT_PRIMER=1
PRIMER_PICK_RIGHT_PRIMER=1
PRIMER_PRODUCT_SIZE_RANGE=%(prodSizeRange)s
SEQUENCE_TARGET=%(targetStart)s,%(targetLen)s
=""" % locals()
        open(tmpInFname, "w").write(conf)

        cmdLine = "primer3_core %s > %s" % (tmpInFname, tmpOutFname)
        runCmd(cmdLine)

        p3 = parseBoulder(tmpOutFname)
        seq1 = p3["PRIMER_LEFT_0_SEQUENCE"]
        seq2 = p3["PRIMER_RIGHT_0_SEQUENCE"]
        tm1 = p3["PRIMER_LEFT_0_TM"]
        tm2 = p3["PRIMER_RIGHT_0_TM"]
        pos1 = int(p3["PRIMER_LEFT_0"].split(",")[0])
        pos2 = int(p3["PRIMER_RIGHT_0"].split(",")[0])
        return seq1, tm1, pos1, seq2, tm2, pos2

def parseFasta(fname):
    " parse a fasta file, where each seq is on a single line, return dict id -> seq "
    seqs = {}
    parts = []
    seqId = None
    for line in open(fname):
        line = line.rstrip("\n")
        if line.startswith(">"):
            if seqId!=None:
                seqs[seqId]  = "".join(parts)
            seqId = line.lstrip(">")
            parts = []
        else:
            parts.append(line)
    if len(parts)!=0:
        seqs[seqId]  = "".join(parts)
    return seqs

def findBestMatch(genome, seq):
    """ find best match for input sequence from batchId in genome and return as
    a string "chrom:start-end:strand or '?' if not found "
    """

    # write seq to tmp file
    tmpFaFh = tempfile.NamedTemporaryFile(prefix="crisporBestMatch", suffix=".fa")
    tmpFaFh.write(">tmp\n%s" % seq)
    tmpFaFh.flush()
    faFname = tmpFaFh.name

    # create temp SAM file
    tmpSamFh = tempfile.NamedTemporaryFile(prefix="crisporBestMatch", suffix=".sam")
    samFname = tmpSamFh.name

    cmd = "BIN/bwa bwasw genomes/%(genome)s/%(genome)s.fa %(faFname)s > %(samFname)s" % locals()
    runCmd(cmd)

    chrom, start, end = None, None, None
    for l in open(samFname):
        if l.startswith("@"):
            continue
        l = l.rstrip("\n")
        fs = l.split("\t")
        qName, flag, rName, pos, mapq, cigar, rnext, pnext, tlen, seq, qual = fs[:11]
        if (int(flag) and 2) == 2:
            strand = "-"
        else:
            strand = "+"
        if not re.compile("[0-9]*").match(cigar):
            continue
        if cigar=="*":
            return "?"
            #errAbort("Sequence not found in genome. Are you sure you have pasted the correct sequence and also selected the right genome?")
        matchLen = int(cigar.replace("M","").replace("S", ""))
        # XX why do we get soft clipped sequences from BWA? repeats?
        chrom, start, end =  rName, int(pos), int(pos)+matchLen
        #print chrom, start, end, strand

    # possible problem: we do not check if a match is really a 100% match.
    if chrom==None:
        errAbort("No perfect match found in genome %s." \
            "Are you sure you have selected the right genome?" % genome)

    # delete the temp files
    tmpSamFh.close()
    tmpFaFh.close()
    return "%s:%d-%d:%s" % (chrom, start, end, strand)

def designPrimer(genome, chrom, start, end, strand, guideStart, batchId):
    " create primer for region around chrom:start-end, write output to batch "
    " returns (leftPrimerSeq, lTm, lPos, rightPrimerSeq, rTm, rPos, amplified sequence)" 
    flankStart = start - 1000
    flankEnd = end + 1000

    if flankStart<0:
        errAbort("Not enough space on genome sequence to design primer. Please design it manually")

    flankFname = join(batchDir, batchId+".inFlank.fa")
    cmd = "twoBitToFa genomes/%(genome)s/%(genome)s.2bit %(flankFname)s -seq=%(chrom)s -start=%(flankStart)d -end=%(flankEnd)d" % locals()
    runCmd(cmd)

    flankSeq = parseFasta(flankFname).values()[0]
    tmpFname = join(batchDir, batchId+".primer3.in")
    tmpOutFname = join(batchDir, batchId+".primer3.out")
    # the guide seq has to be at least 150bp away from the left PCR primer for agarose gels
    lSeq, lTm, lPos, rSeq, rTm, rPos = runPrimer3(flankSeq, tmpFname, tmpOutFname, 1000+guideStart-150, 330, "300-600")
    targetSeq = flankSeq[lPos:rPos+1]
    return lSeq, lTm, lPos, rSeq, rTm, rPos, targetSeq

def markupSeq(seq, start, end):
    " print seq with start-end in bold "
    return seq[:start]+"<u>"+seq[start:end]+"</u>"+seq[end:]

def primerDetailsPage(params):
    """ create primers with primer3 around site identified by pamId in batch
    with batchId. Output primers as html
    """
    batchId, pamId, pam = params["batchId"], params["pamId"], params["pam"]
    inSeq, genome, pamSeq, position = batchParams(batchId)
    seqLen = len(inSeq)
    batchBase = join(batchDir, batchId)

    # find position of guide sequence in genome at MM0
    otBedFname = batchBase+".bed"
    otMatches = parseOfftargets(otBedFname)
    if pamId not in otMatches or 0 not in otMatches[pamId]:
        errAbort("No perfect match found for guide sequence in the genome. Are you sure you have selected the right genome? If you have selected the right genome but pasted a cDNA, please note that sequences that overlap a splice site are not part of the genome and cannot be used as guide sequences.")

    matchList = otMatches[pamId][0] # = get all matches with 0 mismatches
    if len(matchList)!=1:
        errAbort("Multiple perfect matches for this guide sequence. Cannot design primer. Please select another guide sequences or email penigault@tefor.net to discuss your strategy or modifications to this software.")
        # XX we could show a dialog: which match do you want to design primers for?
        # But who would want to use a guide sequence that is not unique?

    chrom, start, end, seq, strand, segType, segName = matchList[0]
    start = int(start)
    end = int(end)

    # retrieve guideSeq + PAM sequence from input sequence
    # XX guide sequence must not appear twice in there
    pamFname = batchBase+".fa"
    pams = parseFasta(pamFname)
    guideSeq = pams[pamId]
    guideStrand = pamId[-1]
    guideSeqWPam = seq

    if strand=="+":
        guideStart = inSeq.find(guideSeq)
        highlightSeq = guideSeqWPam
    else:
        guideStart = inSeq.find(revComp(guideSeq))
        highlightSeq = revComp(guideSeqWPam)

    # matchChrom, matchStart, matchEnd, matchStrand = findBestMatch(genome, batchId)

    lSeq, lTm, lPos, rSeq, rTm, rPos, targetSeq = \
        designPrimer(genome, chrom, start, end, strand, 0, batchId)

    guideStart = targetSeq.find(highlightSeq)
    guideEnd = guideStart + len(highlightSeq)

    #if guideStrand=="+":
        #guideStrand = "forward"
    #else:
        #guideStrand = "reverse"

    if not chrom.startswith("ch"):
        chromLong = "chr"+chrom
    else:
        chromLong = chrom

    seqParts = ["<i><u>%s</u></i>" % targetSeq[:len(lSeq)] ] # primer 1
    seqParts.append("&nbsp;")
    seqParts.append(targetSeq[len(lSeq):guideStart]) # sequence before guide

    seqParts.append("<strong>") 
    seqParts.append(targetSeq[guideStart:guideEnd]) # guide sequence including PAM
    seqParts.append("</strong>")

    seqParts.append(targetSeq[guideEnd:len(targetSeq)-len(rSeq)])# sequence after guide

    seqParts.append("&nbsp;")
    seqParts.append("<i><u>%s</u></i>" % targetSeq[-len(rSeq):]) # primer 2

    targetHtml = "".join(seqParts)

    # prettify guideSeqWPam to highlight the PAM
    guideSeqHtml = "%s %s" % (guideSeqWPam[:-len(pam)], guideSeqWPam[-len(pam):])

    print '''<div style='width: 80%; margin-left:10%; margin-right:10%; text-align:left;'>'''
    print "<h2>Guide sequence: %s</h2>" % (guideSeqHtml)

    print "<h3>Validation primers</h3>"

    primerName = pamId.replace("s", "").replace("-", "rev").replace("+","fw")
    print '<table class="primerTable">'
    print '<tr>'
    print "<td>guideRna%sLeft</td>" % primerName
    print "<td>%s</td>" % (lSeq)
    print "<td>Tm %s</td>" % (lTm)
    print "</tr><tr>"
    print "<td>guideRna%sRight</td>" % primerName
    print "<td>%s</td>" % (rSeq)
    print "<td>Tm %s</td>" % (rTm)
    print '</tr></table>'

    print "<h3>Genome fragment with validation primers and guide sequence</h3>"
    if strand=="-":
        print("Your guide sequence is on the reverse strand relative to the genome sequence, so it is reverse complemented in the sequence below.<p>")

    print '''<div style='word-wrap: break-word; word-break: break-all;'>'''
    print "<strong>Genomic sequence %s:%d-%d including primers, forward strand:</strong><br> <tt>%s</tt><br>" % (chromLong, start, end, targetHtml)
    print '''</div>'''
    print "<strong>Sequence length:</strong> %d<p>" % (rPos-lPos)
    print '<small>Method: Primer3.2 with default settings, target length 400-600 bp</small>'

    # restriction enzymes
    allEnzymes = readEnzymes()
    pamSeq = seq[-len(pam):]
    mutEnzymes = matchRestrEnz(allEnzymes, guideSeq, pamSeq)
    if len(mutEnzymes)!=0:
        print "<h3>Restriction Enzyme Sites for PCR product validation</h3>"

        print "Cas9 induces mutations next to the PAM site."
        print "If a mutation is induced, then it is very likely that one of the followingenzymes no longer cuts your PCR product amplified from the mutant sequence."
        print "For each restriction enzyme, the guide sequence with the restriction site underlined is shown below.<p>"


        for enzName, posList in mutEnzymes.iteritems():
            print "<strong>%s</strong>:" % enzName
            for start, end in posList:
                print markupSeq(guideSeqWPam, start, end)
            print "<br>"

    # primer helper

    print """
    <style>
        table.primerTable {
            border-width: 1px;
            border-color: #DDDDDD;
            border-collapse: collapse;
        }
        table.primerTable td {
            border-width: 1px;
            border-color: #DDDDDD;
            border-collapse: collapse;
        }
    </style>
    """
    print "<hr>"
    print "<h2>Expression of guide RNA</h2>"
    print "Depending on the biological system studied, different options are available for expression of Cas9 and guide RNAs. In zebrafish embryos, guide RNAs and Cas9 are usually made by in vitro transcription and co-injected. In mammalian cells, guide RNAs and Cas9 are usually expressed from transfected plasmid and typically driven by U6 and CMV promoters."

    #print "<h4>Summary of primers</h4>"
    #print '<table class="primerTable">'
    #print '<tr>'
    #print "<td>guideRna%sT7sense</td>" % primerName
    #print "<td><tt>%s</tt></td>" % guideRnaFw
    #print "</tr><tr>"
    #print "<td>guideRna%sT7antisense</td>" % (primerName)
    #print "<td><tt>%s</tt></td>" % (guideRnaRv)
    #print "</tr></table>"

    # T7 from plasmids
    print "<h3>In vitro with T7 RNA polymerase from plasmid DNA</h3>"
    print 'To produce guide RNA by in vitro transcription with T7 RNA polymerase, the guide RNA sequence can be cloned in a variety of plasmids (see <a href="http://addgene.org/crispr/empty-grna-vectors/">AddGene website</a>).<br>'
    print "For the guide sequence %s, the following primers should be ordered for cloning into the BsaI-digested plasmid DR274 generated by the Joung lab<p>" % guideSeqWPam
    if guideSeq.lower().startswith("gg"):
        guideRnaFw = "TA<b>%s</b>" % guideSeq
        guideRnaRv = "AAAC<b>%s</b>" % revComp(guideSeq[2:])
    else:
        guideRnaFw = "TAGG<b>%s</b>" % guideSeq
        guideRnaRv = "AAAC<b>%s</b>" % revComp(guideSeq)


    print '<table class="primerTable">'
    print '<tr>'
    print "<td>guideRna%sT7sense</td>" % primerName
    print "<td><tt>%s</tt></td>" % guideRnaFw
    print "</tr><tr>"
    print "<td>guideRna%sT7antisense</td>" % (primerName)
    print "<td><tt>%s</tt></td>" % (guideRnaRv)
    print "</tr></table>"

    # T7 from primers
    print "<h3>In vitro with T7 polymerase using overlapping oligonucleotides</h3>"
    print "Template for in vitro synthesis of guide RNA with T7 RNA polymerase can be prepared by annealing and primer extension of the following primers:<p>"
    prefix = ""
    if not guideSeq.lower().startswith("gg"):
        prefix = "GG"
    specPrimer = "TAATACGACTCACTATA%s<b>%s</b>GTTTTAGAGCTAGAAATAGCAAG" % (prefix, guideSeq)

    print "guideRNA%sT7PromSense:<br><tt>%s</tt><br>" % (primerName, specPrimer)

    print "guideRNAallT7PromAntisense: <tt>AAAAGCACCGACTCGGTGCCACTTTTTCAAGTTGATAACGGACTAGCCTTATTTTAACTTGCTATTTCTAGCTCTAAAAC</tt><br>"
    print "(constant primer used for all guide RNAs)<p>"

    print 'The protocol for template preparation from oligonucleotides and in-vitro transcription can be found in <a href="http://www.ncbi.nlm.nih.gov/pmc/articles/PMC4038517/?report=classic">Gagnon et al. PLoS ONE 2014</a>.'

    # MAMMALIAN CELLS
    print "<h3>In mammalian cells from plasmid</h3>"
    if "tttt" in guideSeq.lower():
        print "The guide sequence %s contains the motif TTTT, which terminates RNA polymerase. This guide sequence cannot be transcribed in mammalian cells." % guideSeq
    else:
        print "The guide sequence %s does not contain the motif TTTT, which terminates RNA polymerase. This guide sequence can be transcribed in mammalian cells." % guideSeq

    print "<br>"
    print "To express guide RNA in mammalian cells, a variety of plasmids are available. For example, to clone the guide RNA sequence in the plasmid pM3636, where guide RNA expression is driven by a human U6 promoter, the following primers should be used :"
    print "<br>"
    if guideSeq.lower().startswith("g"):
        guideRnaFw = "ACACC<b>%s</b>G" % guideSeq
        guideRnaRv = "AAAAC<b>%s</b>G" % revComp(guideSeq)
    else:
        guideRnaFw = "ACACC<u>G</u><b>%s</b>G" % guideSeq
        guideRnaRv = "AAAAC<b>%s</b><u>C</u>G" % revComp(guideSeq)
        print("<strong>Note:</strong> Efficient transcription from the U6 promoter requires a 5' G. This G has been added to the sequence below.<br>")
    print '<table class="primerTable"><tr>'
    print "<td>guideRNA%sU6sense</td> <td><tt>%s</tt></td>" % (primerName, guideRnaFw)
    print "</tr><tr>"
    print "<td>guideRNA%sU6antisense</td> <td><tt>%s</tt></td>" % (primerName, guideRnaRv)
    print "</tr></table>"

    print "<hr>"
    print '</div>'

def runTests():
    guideSeq = "CTCTTTACGCAGAGGGATGT"
    testRes = {"ATTTTTATGCAGAGTGATGT":     0.4, 
               "TTCTTTACCCGGAGGGATGA": 0.2, 
               "CTGTTTACACACAGGGATTT": 0.2, 
               "CTCTCTGTGCAGATGGATGT": 0.1, 
               "ATCTTAAAGCAGATGGATGT": 0.1, 
               "CTCTTTCCGCAGAGGCTTGT": 0.1, 
               "CTCGTAGCGCAGAGGGAGGT": 0.1, 
               "CTCTTTAAAGAGATGGATGT": 0.1, 
               "CACTTCACTCAGAGGCATGT": 0.1, 
               "CTTTTTTCTCAGAAGGATGT": 0.1, 
               "CTCTTTACACAGAGAGACGT": 0.1, 
               "CTCTTTTCTCAGAGAGATGG": 0.1, 
               "CTATTTACCCAAATGGATGT": 0.1, 
               "CTCTTTGCACAGGGGGAAGT": 0, 
               "CTCTTTGCACAGGGGGAAGT": 0, 
               "CTCTTCACACAGAGGAATGA": 0, 
               "CTCTTTCCACAGGGGAATGT": 0 }

    testRes2 = {
       "GAGTCTAAGCAGAAGAAGAA":     2.2,
       "GAGTCCTAGCAGGAGAAGAA": 1.8,
       "GAGAGCAAGCAGAAGAAGAA": 1.6,
       "GAGTACTAGAAGAAGAAGAA": 1.6,
       "ACGTCTGAGCAGAAGAAGAA": 1.5,
       "GCGACAGAGCAGAAGAAGAA": 1.5,
       "GAGTAGGAGGAGAAGAAGAA": 1.4,
       "GATGCCGTGAAGAAGAAGAA": 1.3,
       "GATTCCTACCAGAAGAAGAA": 1,
       "GAATCCAAGCAGAAGAAGAG": 1,
       "AAGTACTGGCAGAAGAAGAA": 0.9,
       "AGGTGCTAGCAGAAGAAGAA": 0.9,
       "GGGGCCAGGCAGAAGAAGAA": 0.9,
       "ATGTGCAAGCAGAAGAAGAA": 0.9,
       "ACCTCCCAGCAGAAGAAGAA": 0.9,
       "CCCTCCCAGCAGAAGAAGAA": 0.9,
       "TCATCCAAGCAGAAGAAGAA": 0.9,
       "TTCTCCAAGCAGAAGAAGAA": 0.9,
       "GGTGCCAAGCAGAAGAAGAA": 0.9,
       "GCACCCCAGCAGAAGAAGAA": 0.9,
       "CAGTCCAGGAAGAAGAAGAA": 0.9,
       "AAGCCCAAGGAGAAGAAGAA": 0.9,
       "CACTCCAAGTAGAAGAAGAA": 0.9,
       "GAGTCCGGGAAGGAGAAGAA": 0.9,
       "GGTTCCCAGGAGAAGAAGAA": 0.9,
       "AAGTCTGAGCACAAGAAGAA": 0.9,
       "GAGGACAAGAAGAAGAAGAA": 0.9,
       "GTCTGCGATCAGAAGAAGAA": 0.8,
       "GGTTCTGTGCAGAAGAAGAA": 0.8,
       "AGGTGGGAGCAGAAGAAGAA": 0.8,
       "AAGAGCGAGCGGAAGAAGAA": 0.8,
       "CAATTTGAGCAGAAGAAGAA": 0.8,
       "AATACAGAGCAGAAGAAGAA": 0.8,
       "CAAACGGAGCAGAAGAAGAA": 0.8,
       "AAGTGAGAGTAGAAGAAGAA": 0.8,
       "AAGTAGGAGAAGAAGAAGAA": 0.8,
       "AAGTTGGAGAAGAAGAAGAA": 0.8,
       "CAGGCTGAGAAGAAGAAGAA": 0.8,
       "TAGTCAGGGGAGAAGAAGAA": 0.8,
       "TAGTCAGGGGAGAAGAAGAA": 0.8,
       "AAGTGGGAGGAGAAGAAGAA": 0.8,
       "TAGTCAGGGGAGAAGAAGAA": 0.8,
       "TCTTCCGAGCTGAAGAAGAA": 0.8,
       "GCGGCCGATGAGAAGAAGAA": 0.8,
       "GCGTCCGCCAAGAAGAAGAA": 0.8,
       "GCTCCTGAGCAGAAGAAGAA": 0.8,
       "CACTCTGAGGAGAAGAAGAA": 0.8,
       "GTGTGGGAGGAGAAGAAGAA": 0.8,
       "GGGTAAGAGTAGAAGAAGAA": 0.8
    }
    #for seq, expScore in testRes.iteritems():
        #score = calcHitScore(guideSeq, seq)
        #print score, "%0.1f" % score, expScore

    guideSeq = "GAGTCCGAGCAGAAGAAGAA"
    for seq, expScore in testRes2.iteritems():
        score = calcHitScore(guideSeq, seq)
        print score, "%0.1f" % score, expScore
    
def main():
    if len(sys.argv)!=1:
        runTests()
        sys.exit(0)

    cgitb.enable()
    # parse incoming parameters
    params = getParams()
    batchId = None

    if "batchId" in params and "download" in params:
        downloadFile(params)
        return

    # print headers
    # save seq/org/pam into a cookie, if they were provided
    if "seq" in params and "org" in params and "pam" in params:
        seq, org, pam = params["seq"], params["org"], params["pam"]
        saveSeqOrgPamToCookies(seq, org, pam)
        batchId = makeTempBase(seq, org, pam)

    print "Content-type: text/html\n"
    print "" # = end of http headers

    printHeader(batchId)
    printTeforBodyStart()

    printBody(params)     # main dispatcher, branches based on the params dictionary

    printTeforBodyEnd()
    print("</body></html>")

main()
