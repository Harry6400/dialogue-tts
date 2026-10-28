import {ArrowLeft, ArrowRight, Check, ChevronDown, Download, Ellipsis, FileText, FolderOpen, Info, LogOut, Moon, Pause, Play, Plus, RefreshCw, Search, Settings2, Sun, TriangleAlert, Volume2, VolumeX, X} from 'lucide-react';
export type IconName = 'plus' | 'file' | 'play' | 'pause' | 'folder' | 'settings' | 'close' | 'chevron' | 'arrow' | 'back' | 'search' | 'check' | 'warning' | 'refresh' | 'sun' | 'moon' | 'info' | 'more' | 'exit' | 'download' | 'volume' | 'muted';
const icons = {plus:Plus,file:FileText,play:Play,pause:Pause,folder:FolderOpen,settings:Settings2,close:X,chevron:ChevronDown,arrow:ArrowRight,back:ArrowLeft,search:Search,check:Check,warning:TriangleAlert,refresh:RefreshCw,sun:Sun,moon:Moon,info:Info,more:Ellipsis,exit:LogOut,download:Download,volume:Volume2,muted:VolumeX};
export function Icon({name,className}: {name:IconName;className?:string}) {
  const Component=icons[name];
  return <Component className={className} strokeWidth={1.8} aria-hidden="true"/>;
}
