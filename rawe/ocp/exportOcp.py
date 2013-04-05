import subprocess
import ctypes
import os
import sys

from ..utils import codegen
import rawe.dae.rienIntegrator as rienIntegrator
import writeAcadoOcpExport

def makeExportMakefile(options):
    p = subprocess.Popen(['pkg-config', '--libs','ocg2'], stdout = subprocess.PIPE)
    ret = p.wait()
    assert ret==0, 'error calling `pkg-config --libs ocg2`'
    rpath = None
    for blah in p.stdout.read().strip().split(' '):
        blah = blah.strip()
        if len(blah) > 1 and blah[:2] == '-L':
            rpath = blah[2:]
            break
    assert rpath is not None, "couldn't detect the library path of ocg2 :("

    makefile = """\
CXX       = %(CXX)s
CXXFLAGS  = -O2 -fPIC -finline-functions -I. `pkg-config --cflags acado` `pkg-config --cflags ocg2`
LDFLAGS = -lm `pkg-config --libs acado` `pkg-config --libs ocg2`

CXX_SRC = export_ocp.cpp
OBJ = $(CXX_SRC:%%.cpp=%%.o)

.PHONY: clean all export_ocp.so
all : $(OBJ) export_ocp.so

%%.o : %%.cpp #acado.h
\t@echo CXX $@: $(CXX) $(CXXFLAGS) -c $< -o $@
\t@$(CXX) $(CXXFLAGS) -c $< -o $@

export_ocp.so::LDFLAGS+=-Wl,-rpath,%(rpath)s

export_ocp.so : $(OBJ)
\t@echo LD $@ : $(CXX) -shared -o $@ $(OBJ) $(LDFLAGS)
\t@$(CXX) -shared -o $@ $(OBJ) $(LDFLAGS)

clean :
\trm -f *.o *.so
""" % {'CXX':options['CXX'],'rpath':rpath}
    return makefile

def exportOcp(ocp, options, qpSolver):
    supportedQps = ['QP_OASES']
    assert qpSolver in supportedQps, "qp solver must be one of "+str(supportedQps)

    # make sure pkg-config is available
    try:
        subprocess.check_call(["pkg-config", '--version'], \
                                  stdout = subprocess.PIPE, stderr = subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError):
        sys.stderr.write('`pkg-config` has not been found, please install it\n')
        sys.exit(os.EX_CONFIG)

    # make sure pkg-config can find acado and ocg2
    for name in ['acado','ocg2']:
        try:
            call = subprocess.Popen(['pkg-config', '--libs', '--cflags', name], \
                                        stdout = subprocess.PIPE, stderr = subprocess.PIPE)
            (output, error) = call.communicate()
            if error != '':
                raise OSError
        except (subprocess.CalledProcessError, OSError):
            raise Exception('\n'+error)

    # write the ocp exporter cpp file
    genfiles = {'export_ocp.cpp':writeAcadoOcpExport.generateAcadoOcp(ocp),
                'Makefile':makeExportMakefile(options)}
    exportpath = codegen.memoizeFiles(genfiles)

    # compile the ocp exporter
    p = subprocess.Popen(['make',codegen.makeJobs()], stdout=subprocess.PIPE, cwd=exportpath)
    ret = p.wait()
    if ret != 0:
        print "stdout: "+p.stdout.read()
        raise Exception("exportOcp phase 1 compilation failed, return code "+str(ret))

    # load the ocp exporter
    Ni = 5
    lib = ctypes.cdll.LoadLibrary(os.path.join(exportpath, 'export_ocp.so'))

    # run the ocp exporter
    def runOcpExporter(path):
        if qpSolver == 'QP_OASES':
            os.mkdir(os.path.join(path,'qpoases'))

        ret = lib.exportOcp(ocp._nk,
                            Ni,
                            ctypes.c_double(ocp._ts),
                            ctypes.c_char_p(path))
        if ret != 0:
            raise Exception("call to export_ocp.so failed")
    files = codegen.withTempdir(runOcpExporter)

    # add model for rien integrator
    files['model.c'] = '#include "qpoases/solver.hpp"\n\n' + \
        ocp._dae.makeRienModel(ocp._ts)['modelFile']
    if qpSolver is 'QP_OASES':
        ocpret = exportQpOases(options, files)
    else:
        raise Exception('the impossible happened, unsupported qp solver: "'+str(qpSolver)+'"')

    return ocpret


def qpoasesMakefile(options, qposrc):
    qposrc = ' \\\n'.join(['\t'+os.path.join('qpoases', q.split('qpoases'+os.sep)[1]) for q in qposrc])
    makefile = """\
CXX      = %(CXX)s
CC       = %(CC)s
CXXFLAGS = -O3 -fPIC -finline-functions
CFLAGS   = -O3 -fPIC -finline-functions

#CFLAGS   += -Wall -Wextra
#CXXFLAGS += -Wall -Wextra

LDFLAGS = -lm -lrt

CXX_SRC = \\
%(qpo_src)s \\
\tqpoases/solver.cpp

C_SRC = \\
\tacado_integrator.c \\
\tacado_solver.c \\
\tacado_auxiliary_functions.c \\
\tmodel.c \\
\tworkspace.c

QPO_INC = \\
\t-I. \\
\t-Iqpoases \\
\t-Iqpoases/INCLUDE \\
\t-Iqpoases/SRC

CXXFLAGS += $(QPO_INC)

CXX_OBJ = $(CXX_SRC:%%.cpp=%%.o)
C_OBJ = $(C_SRC:%%.c=%%.o)

HEADERS = \\
\tqpoases/solver.hpp \\
\tacado_auxiliary_functions.h \\
\tacado_common.h

.PHONY: clean all ocp.a ocp.so
all : $(CXX_OBJ) $(C_OBJ) ocp.a ocp.so

$(CXX_OBJ) : %%.o : %%.cpp $(HEADERS)
\t@echo CXX $@: $(CXX) $(CXXFLAGS) -c $< -o $@
\t@$(CXX) $(CXXFLAGS) -c $< -o $@

$(C_OBJ) : %%.o : %%.c $(HEADERS)
\t@echo CC $@: $(CC) $(CFLAGS) -c $< -o $@
\t@$(CC) $(CFLAGS) -c $< -o $@

ocp.so : $(CXX_OBJ) $(C_OBJ)
\t@echo LD $@: $(CXX) -shared -o $@ $(CXX_OBJ) $(C_OBJ) $(LDFLAGS)
\t@$(CXX) -shared -o $@ $(CXX_OBJ) $(C_OBJ) $(LDFLAGS)

ocp.a : $(CXX_OBJ) $(C_OBJ)
\t@echo AR $@ : ar r $@ $?
\t@ar r $@ $?

clean :
\t@echo rm -f ocp.so ocp.a $(CXX_OBJ) $(C_OBJ) #*.o *.a ./qpoases/SRC/*.o ./qpoases/SRC/*.a test
\t@rm -f ocp.so ocp.a $(CXX_OBJ) $(C_OBJ)
""" % {'CXX':options['CXX'], 'CC':options['CC'], 'qpo_src':qposrc}
    return makefile

def exportQpOases(options, phase1src):
    # call pkg-config to get qpoases source and includes
    qpoStuff = {}
    for name in ['qpOASESsrc', 'qpOASESinc']:
        p = subprocess.Popen(['pkg-config','--variable',name,'acado'], stdout=subprocess.PIPE)
        assert p.wait() == 0, '"pkg-config --variable '+name+' acado" returned error'
        qpoStuff[name] = (p.stdout.read()).strip()
    qpoStuff['qpOASESsrc'] = qpoStuff['qpOASESsrc'].split(' ')

    # get qpoases source as file dictionary
    qpoSrcPath = os.path.join(qpoStuff['qpOASESsrc'][0].split('qpoases')[0], 'qpoases')
    phase2src = codegen.directoryToDict(qpoSrcPath)

    # merge qpoases source with phase 1 source
    def mergeAll(srcdict,destdict):
        for name,src in srcdict.items():
            if isinstance(src,dict):
                if name not in destdict:
                    destdict[name] = {}
                assert isinstance(destdict[name], dict), "dictionary merge failed, source was directory but destination was a file"
                destdict[name] = mergeAll(src,destdict[name])
            else:
                destdict[name] = src
        return destdict
    genfiles = mergeAll(phase1src, {'qpoases':phase2src})

    # add makefile
    genfiles['Makefile'] = qpoasesMakefile(options, qpoStuff['qpOASESsrc'])
    genfiles['workspace.c'] ='''\
#include "acado_common.h"
ACADOworkspace acadoWorkspace;
ACADOvariables acadoVariables;
'''

    # write all this
    exportpath = codegen.memoizeFiles(genfiles)
    print exportpath

    # compile!
    p = subprocess.Popen(['make',codegen.makeJobs()], cwd=exportpath)
#    p = subprocess.Popen(['make',codegen.makeJobs()], stdout=subprocess.PIPE, cwd=exportpath)
    ret = p.wait()
    if ret != 0:
#        print "stdout: "+p.stdout.read()
        raise Exception("ocp compilation failed, return code "+str(ret))

    # load the result
    libpath = os.path.join(exportpath, 'ocp.so')

    class OcpRT(object):
        def __init__(self,libpath):
            print 'loading "'+libpath+'"'
            self._lib = ctypes.cdll.LoadLibrary(libpath)
            print 'initializing solver'
            self._lib.initializeSolver()
            self._libpath = libpath

        def preparationStep(self):
            self._lib.preparationStep()

        def feedbackStep(self):
            return self._lib.feedbackStep()

        def initializeNodesByForwardSimulation(self):
            self._lib.initializeNodesByForwardSimulation()

#        def shiftStates( int strategy, real_t* const xEnd, real_t* const uEnd ):
#            void shiftStates( int strategy, real_t* const xEnd, real_t* const uEnd );

#        def shiftControls( real_t* const uEnd ):
#            void shiftControls( real_t* const uEnd );

        def getKKT(self):
            return self._lib.getKKT()

        def getObjective(self):
            return self._lib.getObjective()
    return OcpRT(libpath)
#    def call(path):
#        ret = lib.makeRienIntegrator(ctypes.c_char_p(path),
#                                     options['numIntervals'],
#                                     ctypes.c_double(1.0),
#                                     ctypes.c_char_p(options['integratorType']),
#                                     options['integratorGrid'],
#                                     options['numIntegratorSteps'],
#                                     nx, nz, nup)
#        if ret != 0:
