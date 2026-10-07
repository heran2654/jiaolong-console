// SPDX-License-Identifier: GPL-2.0-or-later
/* MRID6-23 adapter. Protocol recovered from the owner's factory application.
 * Uses only the documented MICommonInterface WMI method, never raw EC writes or direct SMU writes.
 * In particular, original MRID6 SET20 uses byte[4], unlike some other boards.
 */
#include <linux/module.h>
#include <linux/acpi.h>
#include <linux/wmi.h>
#include <linux/dmi.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/unaligned.h>
#include <linux/delay.h>

#define GUID "B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B"
struct jl_data { struct wmi_device *wdev; struct mutex lock; };

static bool model_ok(void)
{
 return dmi_match(DMI_SYS_VENDOR,"MECHREVO") &&
        dmi_match(DMI_BOARD_NAME,"MRID6-23") &&
        dmi_match(DMI_BIOS_VERSION,"MRID6_23_P_V36");
}
/* Caller holds lock; original application always sends a zero-filled 32-byte packet. */
static int call(struct jl_data *d, u8 op, u8 cmd, const u8 *payload, size_t len, u8 *result)
{
 u8 packet[32]={0};
 struct wmi_buffer in={.length=32,.data=packet},out={0};
 int ret;
 if (!model_ok() || len>28) return -ENODEV;
 packet[1]=op; packet[3]=cmd;
 if (payload) memcpy(packet+4,payload,len);
 ret=wmidev_invoke_method(d->wdev,0,1,&in,&out);
 if (!ret && result) {
  if (!out.data || out.length!=32) ret=-EPROTO;
  else memcpy(result,out.data,32);
 }
 kfree(out.data);
 return ret;
}
static int get(struct jl_data *d,u8 cmd,u8 *result) { return call(d,250,cmd,NULL,0,result); }
static int value(struct jl_data *d,u8 cmd)
{
 u8 r[32]; int ret=get(d,cmd,r);
 return ret ? ret : r[4];
}
static ssize_t state_show(struct device *dev,struct device_attribute *attr,char *buf)
{
 struct jl_data *d=dev_get_drvdata(dev);
 u8 fan[32]={0},color[32]={0};
 int p,g,b,m,fn,tp,ac,t,fr,cr,fo,fmax,custom,ambient;
 u8 spl=0,sppt=0,twall=0;int er1,er2,er3;
 mutex_lock(&d->lock);
 p=value(d,8); g=value(d,9); b=value(d,18); m=value(d,16);
 fn=value(d,11); tp=value(d,12); ac=value(d,19); t=value(d,22);
 fr=get(d,13,fan); cr=get(d,17,color);
 fo=value(d,20); fmax=value(d,21); custom=value(d,23); ambient=value(d,15);
 /* Read only these three EC fields whose addresses were verified in THIS BIOS DSDT. */
 er1=ec_read(0xf6,&spl);er2=ec_read(0xf7,&sppt);er3=ec_read(0xf8,&twall);
 mutex_unlock(&d->lock);
 return sysfs_emit(buf,"{\"profile\":%d,\"gpu_mode\":%d,\"kb_brightness\":%d,\"kb_mode\":%d,\"fn_lock\":%d,\"touchpad_lock\":%d,\"ac_type\":%d,\"cpu_temp\":%d,\"fan_cpu_rpm\":%d,\"fan_gpu_rpm\":%d,\"kb_color\":[%d,%d,%d],\"fan_override\":%d,\"fan_max_rpm\":%d,\"cpu_custom_mode\":%d,\"ambient_light\":%d,\"cpu_spl_w\":%d,\"cpu_sppt_w\":%d,\"cpu_temp_wall\":%d}\n",
 p,g,b,m,fn,tp,ac,t,fr?fr:get_unaligned_le16(fan+4),fr?fr:get_unaligned_le16(fan+6),cr?cr:color[4],cr?cr:color[5],cr?cr:color[6],fo,fmax<0?fmax:fmax*100,custom,ambient,er1?er1:spl,er2?er2:sppt,er3?er3:twall);
}
static DEVICE_ATTR_RO(state);
static ssize_t fan_status_show(struct device *dev,struct device_attribute *attr,char *buf)
{
 struct jl_data *d=dev_get_drvdata(dev);u8 a[32]={0},b[32]={0};int ar,br;
 mutex_lock(&d->lock);ar=get(d,20,a);br=get(d,21,b);mutex_unlock(&d->lock);
 return sysfs_emit(buf,"get20=%d %*ph get21=%d %*ph\n",ar,32,a,br,32,b);
}
static DEVICE_ATTR_RO(fan_status);


static ssize_t scalar_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{
 struct jl_data *d=dev_get_drvdata(dev); unsigned int v; u8 cmd,max,payload; int ret;
 if (kstrtouint(buf,10,&v)) return -EINVAL;
 if (!strcmp(attr->attr.name,"profile")) { cmd=8; max=2; }
 else if (!strcmp(attr->attr.name,"gpu_mode")) { cmd=9; max=1; }
 else if (!strcmp(attr->attr.name,"kb_brightness")) { cmd=18; max=3; }
 else if (!strcmp(attr->attr.name,"kb_mode")) { cmd=16; max=3; }
 else if (!strcmp(attr->attr.name,"cpu_custom_mode")) { cmd=23; max=1; }
 else if (!strcmp(attr->attr.name,"ambient_light")) { cmd=15; max=1; }
 else if (!strcmp(attr->attr.name,"fn_lock")) { cmd=11; max=1; }
 else if (!strcmp(attr->attr.name,"touchpad_lock")) { cmd=12; max=1; }
 else return -EINVAL;
 if (v>max) return -ERANGE;
 payload=v;
 mutex_lock(&d->lock);
 ret=call(d,251,cmd,&payload,1,NULL);
 mutex_unlock(&d->lock);
 return ret?ret:count;
}
#define SCALAR(name) static struct device_attribute dev_attr_##name=__ATTR(name,0200,NULL,scalar_store)
SCALAR(cpu_custom_mode); SCALAR(ambient_light); SCALAR(profile); SCALAR(gpu_mode); SCALAR(kb_brightness); SCALAR(kb_mode); SCALAR(fn_lock); SCALAR(touchpad_lock);

static ssize_t kb_color_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{
 struct jl_data *d=dev_get_drvdata(dev); unsigned int r,g,b; char extra; u8 p[3]; int ret;
 if(sscanf(buf,"%u %u %u %c",&r,&g,&b,&extra)!=3 || r>255 || g>255 || b>255) return -EINVAL;
 p[0]=r;p[1]=g;p[2]=b;
 mutex_lock(&d->lock);
 ret=call(d,251,17,p,3,NULL);
 mutex_unlock(&d->lock);
 return ret?ret:count;
}
static DEVICE_ATTR_WO(kb_color);

static ssize_t fan_auto_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{
 struct jl_data *d=dev_get_drvdata(dev); u8 zero=0; int ret;
 if(!sysfs_streq(buf,"1")) return -EINVAL;
 mutex_lock(&d->lock); ret=call(d,251,20,&zero,1,NULL); mutex_unlock(&d->lock);
 return ret?ret:count;
}
static DEVICE_ATTR_WO(fan_auto);
static ssize_t fan_rpm_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{
 struct jl_data *d=dev_get_drvdata(dev); unsigned int rpm; u8 enable=1,target; int profile,ret,lo,hi;
 if(kstrtouint(buf,10,&rpm) || rpm%100) return -EINVAL;
 mutex_lock(&d->lock);
 if (!strcmp(attr->attr.name,"fan_custom_rpm")) { lo=2200;hi=5800; }
 else {
  profile=value(d,8);
  switch(profile) { case 0:lo=3500;hi=5000;break;case 1:lo=5000;hi=5800;break;case 2:lo=2200;hi=3500;break;default:ret=-EPROTO;goto done; }
 }
 if(rpm<lo || rpm>hi) {ret=-ERANGE;goto done;}
 target=rpm/100;
 ret=call(d,251,20,&enable,1,NULL);
 if(!ret) ret=call(d,251,21,&target,1,NULL);
 if(ret) { enable=0;call(d,251,20,&enable,1,NULL); }
 done:mutex_unlock(&d->lock);return ret?ret:count;
}
static DEVICE_ATTR_WO(fan_rpm);
static struct device_attribute dev_attr_fan_custom_rpm=__ATTR(fan_custom_rpm,0200,NULL,fan_rpm_store);
static ssize_t cpu_limits_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{
 struct jl_data *d=dev_get_drvdata(dev);unsigned int spl,sppt,temp;char extra;u8 p[2];int ret;
 if(sscanf(buf,"%u %u %u %c",&spl,&sppt,&temp,&extra)!=3)return -EINVAL;
 if(spl<25 || spl>110 || sppt<spl || sppt>150 || temp<60 || temp>100)return -ERANGE;
 mutex_lock(&d->lock);
 if(value(d,23)!=1){ret=-EPERM;goto done;}
 p[0]=2;p[1]=spl;ret=call(d,251,23,p,2,NULL);
 if(!ret){p[0]=3;p[1]=sppt;ret=call(d,251,23,p,2,NULL);}
 if(!ret){p[0]=4;p[1]=temp;ret=call(d,251,23,p,2,NULL);}
 done:mutex_unlock(&d->lock);return ret?ret:count;
}
static DEVICE_ATTR_WO(cpu_limits);
static struct attribute *attrs[]={&dev_attr_state.attr,&dev_attr_fan_status.attr,&dev_attr_profile.attr,&dev_attr_gpu_mode.attr,
 &dev_attr_kb_brightness.attr,&dev_attr_kb_mode.attr,&dev_attr_kb_color.attr,&dev_attr_fn_lock.attr,
 &dev_attr_touchpad_lock.attr,&dev_attr_cpu_custom_mode.attr,&dev_attr_cpu_limits.attr,&dev_attr_ambient_light.attr,&dev_attr_fan_auto.attr,&dev_attr_fan_rpm.attr,&dev_attr_fan_custom_rpm.attr,NULL};
static const struct attribute_group group={.name="jiaolong",.attrs=attrs};
static int probe(struct wmi_device *wdev,const void *context)
{
 struct jl_data *d; int profile,ret;
 if(!model_ok()) return -ENODEV;
 d=devm_kzalloc(&wdev->dev,sizeof(*d),GFP_KERNEL);if(!d)return -ENOMEM;
 d->wdev=wdev;mutex_init(&d->lock);dev_set_drvdata(&wdev->dev,d);
 mutex_lock(&d->lock);profile=value(d,8);mutex_unlock(&d->lock);
 if(profile<0 || profile>2)return -EPROTO;
 ret=devm_device_add_group(&wdev->dev,&group);
 if(!ret)dev_info(&wdev->dev,"MRID6 firmware handshake succeeded, profile=%d (probe is read-only)\n",profile);
 return ret;
}
static const struct wmi_device_id ids[]={{GUID,NULL},{}};
MODULE_DEVICE_TABLE(wmi,ids);
static struct wmi_driver jl_driver={.driver={.name="jiaolong-wmi"},.id_table=ids,.probe=probe};
module_wmi_driver(jl_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("MRID6-23 P_V36 factory-protocol WMI adapter for Jiaolong Console");
MODULE_VERSION("0.4");
