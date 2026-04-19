@echo off
SET NSSM=C:\tools\nssm.exe
%NSSM% stop HXIOptimizer
%NSSM% remove HXIOptimizer confirm
echo Service removed.
